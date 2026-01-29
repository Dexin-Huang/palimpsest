from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Optional

from dotenv import load_dotenv
from google import genai
from google.genai import types

from palimpsest.pipeline.ocr import extract_agentic_vision_response

from .config import RunConfig
from .flags import load_page_flags
from .io import atomic_write_text, hash_text, validate_json_file, validate_json_text
from .runlog import RunLogger, build_status_snapshot, get_git_commit, start_run
from .trace import save_trace

PROJECT_ROOT = Path(__file__).resolve().parents[2]
ENV_PATH = PROJECT_ROOT / ".env"
if ENV_PATH.exists():
    load_dotenv(ENV_PATH)

BACKOFF_BASE = 2


@dataclass
class RunContext:
    run_id: str
    logger: RunLogger
    trace_dir: Path
    prompt_pair: tuple[str, str]
    prompt_hashes: dict[str, str]
    expected_stems: list[str]
    out_dir: Path
    skip_pass2: dict[str, str]

    def event(self, stage: str, status: str, extra: Optional[dict] = None) -> None:
        payload = {
            "timestamp": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
            "run_id": self.run_id,
            "stage": stage,
            "status": status,
        }
        if extra:
            payload.update(extra)
        self.logger.event(payload)

    def page_event(self, page: str, stage: str, status: str, extra: Optional[dict] = None) -> None:
        payload = {
            "timestamp": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
            "run_id": self.run_id,
            "page": page,
            "stage": stage,
            "status": status,
        }
        if extra:
            payload.update(extra)
        self.logger.event(payload)

    def page_error(self, page: str, stage: str, error: str, extra: Optional[dict] = None) -> None:
        payload = {
            "timestamp": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
            "run_id": self.run_id,
            "page": page,
            "stage": stage,
            "error": error,
        }
        if extra:
            payload.update(extra)
        self.logger.error(payload)

    def update_status(self) -> None:
        self.logger.write_status(build_status_snapshot(self.out_dir, self.expected_stems))


def build_run_meta(
    image_dir: Path,
    pattern: str,
    out_dir: Path,
    run_config: RunConfig,
    image_count: int,
) -> dict:
    return {
        "run_id": datetime.utcnow().strftime("%Y%m%d_%H%M%S"),
        "started_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "model": run_config.model,
        "pass_mode": run_config.pass_mode,
        "prompt_set": run_config.prompt.prompt_set,
        "prompt_name": run_config.prompt.prompt_name,
        "prompt_pass1": run_config.prompt.prompt_pass1,
        "prompt_pass2": run_config.prompt.prompt_pass2,
        "image_dir": str(image_dir),
        "pattern": pattern,
        "workers": run_config.workers,
        "max_attempts": run_config.max_attempts,
        "trace": run_config.trace,
        "shard_count": run_config.shard_count,
        "shard_index": run_config.shard_index,
        "output_dir": str(out_dir),
        "git_commit": get_git_commit(PROJECT_ROOT),
        "image_count": image_count,
    }


def prepare_context(out_dir: Path, run_meta: dict, prompt_pair: tuple[str, str], expected_stems: list[str]) -> RunContext:
    prompt_hashes = {
        "pass1": hash_text(prompt_pair[0]),
        "pass2": hash_text(prompt_pair[1]),
    }
    run_meta = dict(run_meta)
    run_meta["prompt_hashes"] = prompt_hashes
    run_id, logger, trace_dir = start_run(out_dir, run_meta, prompt_pair[0], prompt_pair[1])
    flags = load_page_flags(out_dir)
    return RunContext(
        run_id=run_id,
        logger=logger,
        trace_dir=trace_dir,
        prompt_pair=prompt_pair,
        prompt_hashes=prompt_hashes,
        expected_stems=expected_stems,
        out_dir=out_dir,
        skip_pass2=flags.get("skip_pass2", {}),
    )


def transcribe_pass1(
    client: genai.Client,
    image_path: Path,
    prompt: str,
    model: str,
    output_format: str,
) -> object:
    image_bytes = image_path.read_bytes()
    image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")

    config = types.GenerateContentConfig(
        tools=[types.Tool(code_execution={})],
        temperature=0.1,
    )
    if output_format == "json":
        config.response_mime_type = "application/json"

    return client.models.generate_content(
        model=model,
        contents=[prompt, image_part],
        config=config,
    )


def transcribe_pass2(
    client: genai.Client,
    image_path: Path,
    draft: str,
    refine_prompt_template: str,
    model: str,
    output_format: str,
) -> object:
    image_bytes = image_path.read_bytes()
    image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")
    prompt = refine_prompt_template.replace("{draft_transcription}", draft)

    config = types.GenerateContentConfig(
        tools=[types.Tool(code_execution={})],
        temperature=0.1,
    )
    if output_format == "json":
        config.response_mime_type = "application/json"

    return client.models.generate_content(
        model=model,
        contents=[prompt, image_part],
        config=config,
    )


def _attempt_pass(
    *,
    stage: str,
    prompt_hash: str,
    page_name: str,
    max_attempts: int,
    call_fn,
    trace_fn,
    context: RunContext,
    start_extra: Optional[dict] = None,
) -> str:
    for attempt in range(1, max_attempts + 1):
        extra = {"attempt": attempt, "prompt_hash": prompt_hash}
        if start_extra:
            extra.update(start_extra)
        context.page_event(page_name, stage, "start", extra)
        try:
            response = call_fn()
            agentic = extract_agentic_vision_response(response)
            text = agentic.text or ""
            trace_fn(agentic, response, text)
            return text
        except Exception as exc:
            context.page_error(page_name, stage, str(exc), {"attempt": attempt, "prompt_hash": prompt_hash})
            if attempt == max_attempts:
                raise
            time.sleep(BACKOFF_BASE ** attempt)
    raise RuntimeError(f"Unreachable {stage} loop")


def _analyze_pass1(text: str) -> dict:
    try:
        data = json.loads(text)
    except Exception:
        return {"total_lines": 0, "total_chars": 0, "columns": 0, "notes": []}

    columns = data.get("columns", [])
    page_type = data.get("page_type")
    total_lines = 0
    total_chars = 0
    if isinstance(columns, list):
        for col in columns:
            lines = col.get("lines", []) if isinstance(col, dict) else []
            if isinstance(lines, list):
                total_lines += len(lines)
                total_chars += sum(len(str(line.get("diplomatic", ""))) for line in lines if isinstance(line, dict))
    notes = data.get("page_notes", [])
    if not isinstance(notes, list):
        notes = []
    return {
        "total_lines": total_lines,
        "total_chars": total_chars,
        "columns": len(columns) if isinstance(columns, list) else 0,
        "notes": notes,
        "page_type": page_type,
    }


def _is_non_text(metrics: dict) -> tuple[bool, str]:
    total_lines = metrics.get("total_lines", 0)
    total_chars = metrics.get("total_chars", 0)
    notes = " ".join(str(n).lower() for n in metrics.get("notes", []))
    page_type = str(metrics.get("page_type") or "").lower()
    if page_type and page_type not in ("text_page", "mixed"):
        return True, f"page_type:{page_type}"
    if total_lines == 0:
        return True, "no_lines"
    if total_lines <= 3 and total_chars <= 30:
        return True, "very_low_text"
    if any(token in notes for token in ("blank", "no text", "cover", "ownership", "ex libris")):
        return True, "notes_flag"
    return False, ""


def transcribe_page(
    image_path: Path,
    out_dir: Path,
    context: RunContext,
    run_config: RunConfig,
    client: Optional[genai.Client] = None,
) -> dict:
    base = image_path.stem
    ext = "json" if run_config.output_format == "json" else "txt"
    pass1_path = out_dir / f"{base}_pass1.{ext}"
    pass2_path = out_dir / f"{base}_final.{ext}"
    pass1_prompt, refine_prompt = context.prompt_pair
    pass1_hash = context.prompt_hashes["pass1"]
    pass2_hash = context.prompt_hashes["pass2"]
    do_pass1 = run_config.pass_mode in ("both", "pass1")
    do_pass2 = run_config.pass_mode in ("both", "pass2")

    result = {
        "image": image_path.name,
        "pass1_path": pass1_path,
        "pass2_path": pass2_path,
        "pass1_chars": 0,
        "pass2_chars": 0,
        "status": "pending",
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    if client is None:
        client = genai.Client()

    pass1_text: Optional[str] = None
    if run_config.skip_existing and pass1_path.exists():
        json_error = validate_json_file(pass1_path) if run_config.output_format == "json" else None
        if json_error:
            context.page_error(image_path.name, "pass1_validate", json_error)
        else:
            if run_config.verbose and do_pass1:
                print("  Pass 1: SKIPPED (exists)")
            pass1_text = pass1_path.read_text(encoding="utf-8")
            context.page_event(image_path.name, "pass1", "skipped", {"path": str(pass1_path), "prompt_hash": pass1_hash})

    if do_pass1 and pass1_text is None:
        if run_config.verbose:
            print("  Pass 1: Processing...", end=" ", flush=True)

        def call_pass1():
            return transcribe_pass1(
                client, image_path, pass1_prompt, run_config.model, run_config.output_format
            )

        def trace_pass1(agentic, response, text: str) -> None:
            if run_config.trace:
                save_trace(
                    context.trace_dir,
                    base,
                    "pass1",
                    agentic,
                    response,
                    run_config.model,
                    pass1_hash,
                )
            json_error = validate_json_text(text) if run_config.output_format == "json" else None
            if json_error:
                invalid_path = out_dir / f"{base}_pass1.invalid.{ext}"
                atomic_write_text(invalid_path, text)
                raise ValueError(f"invalid JSON: {json_error}")
            atomic_write_text(pass1_path, text)

        try:
            pass1_text = _attempt_pass(
                stage="pass1",
                prompt_hash=pass1_hash,
                page_name=image_path.name,
                max_attempts=run_config.max_attempts,
                call_fn=call_pass1,
                trace_fn=trace_pass1,
                context=context,
            )
            if run_config.verbose:
                print(f"OK ({len(pass1_text)} chars)")
            context.page_event(
                image_path.name,
                "pass1",
                "complete",
                {"path": str(pass1_path), "chars": len(pass1_text)},
            )
        except Exception as exc:
            result["status"] = f"pass1 error: {exc}"
            if run_config.verbose:
                print(f"FAILED: {exc}")
            return result

    if not do_pass1 and pass1_text is None and do_pass2:
        if pass1_path.exists():
            json_error = validate_json_file(pass1_path) if run_config.output_format == "json" else None
            if json_error:
                context.page_error(image_path.name, "pass1_validate", json_error)
            pass1_text = pass1_path.read_text(encoding="utf-8")
        else:
            msg = "missing pass1 for pass2"
            context.page_error(image_path.name, "pass2", msg)
            result["status"] = f"pass2 error: {msg}"
            return result

    result["pass1_chars"] = len(pass1_text or "")

    if pass1_text and run_config.output_format == "json":
        metrics = _analyze_pass1(pass1_text)
        is_non_text, reason = _is_non_text(metrics)
        if is_non_text:
            context.logger.report(
                "non_text",
                {
                    "timestamp": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
                    "run_id": context.run_id,
                    "page": image_path.name,
                    "reason": reason,
                    "metrics": metrics,
                },
            )
        if metrics.get("page_type"):
            context.logger.report(
                "page_type",
                {
                    "timestamp": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
                    "run_id": context.run_id,
                    "page": image_path.name,
                    "page_type": metrics.get("page_type"),
                },
            )
        if do_pass2 and run_config.auto_skip_non_text and is_non_text:
            pass2_text = pass1_text
            if not pass2_path.exists() or not run_config.skip_existing:
                atomic_write_text(pass2_path, pass2_text)
            context.page_event(
                image_path.name,
                "pass2",
                "skipped_non_text",
                {"path": str(pass2_path), "reason": reason},
            )
            result["pass2_chars"] = len(pass2_text or "")
            result["status"] = "complete"
            return result

    if do_pass2 and base in context.skip_pass2:
        reason = context.skip_pass2.get(base, "manual")
        if not pass1_text and pass1_path.exists():
            pass1_text = pass1_path.read_text(encoding="utf-8")
        if pass1_text:
            pass2_text = pass1_text
            if not pass2_path.exists() or not run_config.skip_existing:
                atomic_write_text(pass2_path, pass2_text)
            context.page_event(
                image_path.name,
                "pass2",
                "skipped_manual",
                {"path": str(pass2_path), "reason": reason},
            )
            result["pass2_chars"] = len(pass2_text or "")
            result["status"] = "complete"
            return result
        context.page_error(image_path.name, "pass2", "skip_pass2_missing_pass1")

    pass2_text: Optional[str] = None
    if do_pass2 and run_config.skip_existing and pass2_path.exists():
        json_error = validate_json_file(pass2_path) if run_config.output_format == "json" else None
        if json_error:
            context.page_error(image_path.name, "pass2_validate", json_error)
        else:
            if run_config.verbose:
                print("  Pass 2: SKIPPED (exists)")
            pass2_text = pass2_path.read_text(encoding="utf-8")
            context.page_event(image_path.name, "pass2", "skipped", {"path": str(pass2_path), "prompt_hash": pass2_hash})

    if do_pass2 and pass2_text is None:
        if run_config.verbose:
            print("  Pass 2: Refining...", end=" ", flush=True)

        draft_hash = hash_text(pass1_text or "")

        def call_pass2():
            return transcribe_pass2(
                client,
                image_path,
                pass1_text or "",
                refine_prompt,
                run_config.model,
                run_config.output_format,
            )

        def trace_pass2(agentic, response, text: str) -> None:
            if run_config.trace:
                save_trace(
                    context.trace_dir,
                    base,
                    "pass2",
                    agentic,
                    response,
                    run_config.model,
                    pass2_hash,
                    extra={"draft_hash": draft_hash},
                )
            json_error = validate_json_text(text) if run_config.output_format == "json" else None
            if json_error:
                invalid_path = out_dir / f"{base}_final.invalid.{ext}"
                atomic_write_text(invalid_path, text)
                raise ValueError(f"invalid JSON: {json_error}")
            atomic_write_text(pass2_path, text)

        try:
            pass2_text = _attempt_pass(
                stage="pass2",
                prompt_hash=pass2_hash,
                page_name=image_path.name,
                max_attempts=run_config.max_attempts,
                call_fn=call_pass2,
                trace_fn=trace_pass2,
                context=context,
                start_extra={"draft_hash": draft_hash},
            )
            if run_config.verbose:
                print(f"OK ({len(pass2_text)} chars)")
            context.page_event(
                image_path.name,
                "pass2",
                "complete",
                {"path": str(pass2_path), "chars": len(pass2_text), "draft_hash": draft_hash},
            )
        except Exception as exc:
            result["status"] = f"pass2 error: {exc}"
            if run_config.verbose:
                print(f"FAILED: {exc}")
            return result

    result["pass2_chars"] = len(pass2_text or "")
    if do_pass2:
        result["status"] = "complete"
    else:
        result["status"] = "pass1_complete"
    return result


def _print_header(images: list[Path], total_images: int, pattern: str, run_config: RunConfig, out_dir: Path) -> None:
    print("Two-Pass Manuscript Transcription Pipeline")
    print("=" * 60)
    if run_config.shard_count > 1:
        print(f"Images: {len(images)} in shard {run_config.shard_index + 1}/{run_config.shard_count} (of {total_images})")
    else:
        print(f"Images: {len(images)} matching '{pattern}'")
    if run_config.prompt.prompt_set:
        print(f"Prompt set: {run_config.prompt.prompt_set}")
    elif run_config.prompt.prompt_pass1 and run_config.prompt.prompt_pass2:
        print(f"Prompt files: {run_config.prompt.prompt_pass1}, {run_config.prompt.prompt_pass2}")
    else:
        print(f"Prompt: {run_config.prompt.prompt_name}")
    print(f"Model: {run_config.model}")
    print(f"Output: {out_dir}")
    print("=" * 60)


def _summarize(results: list[dict], verbose: bool, pass_mode: str) -> tuple[int, int]:
    if pass_mode == "pass1":
        complete = sum(1 for r in results if r["status"] in ("complete", "pass1_complete"))
    else:
        complete = sum(1 for r in results if r["status"] == "complete")
    failed = len(results) - complete
    print(f"\n{'=' * 60}")
    print(f"Complete: {complete} pages, Failed: {failed} pages")

    if verbose:
        print("\nDetails:")
        for r in results:
            improvement = ""
            if r["pass1_chars"] > 0 and r["pass2_chars"] > 0:
                pct = (r["pass2_chars"] - r["pass1_chars"]) / r["pass1_chars"] * 100
                improvement = f" (+{pct:.0f}%)" if pct > 0 else f" ({pct:.0f}%)"
            print(f"  {r['image']}: {r['pass1_chars']} -> {r['pass2_chars']} chars{improvement} [{r['status']}]")
    return complete, failed


def _maybe_assemble(out_dir: Path, output_format: str, pass_mode: str) -> None:
    if output_format != "json" or pass_mode == "pass1":
        return
    try:
        from palimpsest.book import assemble_book

        index = assemble_book(out_dir)
        print(f"\nBook assembled: {index['total_pages']} pages")
        if index.get("missing_final"):
            print(f"Missing final pages: {len(index['missing_final'])}")
    except Exception as exc:
        print(f"Book assembly failed: {exc}")


def run_single(image_path: Path, out_dir: Path, run_config: RunConfig) -> dict:
    prompt_pair = run_config.prompt.load()
    run_meta = build_run_meta(
        image_dir=image_path.parent,
        pattern=image_path.name,
        out_dir=out_dir,
        run_config=run_config,
        image_count=1,
    )
    context = prepare_context(out_dir, run_meta, prompt_pair, [image_path.stem])
    client = genai.Client()
    result = transcribe_page(
        image_path=image_path,
        out_dir=out_dir,
        context=context,
        run_config=run_config,
        client=client,
    )
    context.update_status()
    if run_config.pass_mode == "pass1":
        is_complete = result["status"] in ("complete", "pass1_complete")
    else:
        is_complete = result["status"] == "complete"
    context.event(
        "run",
        "complete",
        {
            "complete_pages": 1 if is_complete else 0,
            "failed_pages": 0 if is_complete else 1,
        },
    )
    _maybe_assemble(out_dir, run_config.output_format, run_config.pass_mode)
    return result


def run_batch(image_dir: Path, out_dir: Path, pattern: str, run_config: RunConfig) -> list[dict]:
    all_images = sorted(image_dir.glob(pattern))
    if not all_images:
        print(f"No images found matching '{pattern}' in {image_dir}")
        return []

    total_images = len(all_images)
    images = all_images
    if run_config.shard_count > 1:
        if run_config.shard_index < 0 or run_config.shard_index >= run_config.shard_count:
            raise ValueError("shard_index must be in [0, shard_count)")
        images = [
            img for idx, img in enumerate(all_images)
            if idx % run_config.shard_count == run_config.shard_index
        ]

    prompt_pair = run_config.prompt.load()
    expected_stems = [img.stem for img in all_images]
    run_meta = build_run_meta(
        image_dir=image_dir,
        pattern=pattern,
        out_dir=out_dir,
        run_config=run_config,
        image_count=len(images),
    )
    if run_config.shard_count > 1:
        run_meta["image_total"] = total_images
    context = prepare_context(out_dir, run_meta, prompt_pair, expected_stems)

    _print_header(images, total_images, pattern, run_config, out_dir)

    results: list[dict] = []
    ext = "json" if run_config.output_format == "json" else "txt"

    if run_config.workers <= 1:
        client = genai.Client()
        for i, image_path in enumerate(images, 1):
            print(f"\n[{i}/{len(images)}] {image_path.name}")
            result = transcribe_page(
                image_path=image_path,
                out_dir=out_dir,
                context=context,
                run_config=run_config,
                client=client,
            )
            results.append(result)
            context.update_status()
            if run_config.delay > 0 and i < len(images):
                time.sleep(run_config.delay)
    else:
        from concurrent.futures import ThreadPoolExecutor, as_completed

        def _worker(img_path: Path) -> dict:
            return transcribe_page(
                image_path=img_path,
                out_dir=out_dir,
                context=context,
                run_config=run_config,
                client=None,
            )

        print(f"Workers: {run_config.workers}")
        with ThreadPoolExecutor(max_workers=run_config.workers) as executor:
            futures = {executor.submit(_worker, img): img for img in images}
            for i, future in enumerate(as_completed(futures), 1):
                img = futures[future]
                try:
                    result = future.result()
                    results.append(result)
                    print(f"[{i}/{len(images)}] {img.name} [{result['status']}]")
                    context.update_status()
                except Exception as exc:
                    results.append({
                        "image": img.name,
                        "status": f"error: {exc}",
                        "pass1_chars": 0,
                        "pass2_chars": 0,
                        "pass1_path": out_dir / f"{img.stem}_pass1.{ext}",
                        "pass2_path": out_dir / f"{img.stem}_final.{ext}",
                    })
                    context.update_status()

    complete, failed = _summarize(results, run_config.verbose, run_config.pass_mode)
    _maybe_assemble(out_dir, run_config.output_format, run_config.pass_mode)
    context.update_status()
    context.event(
        "run",
        "complete",
        {
            "complete_pages": complete,
            "failed_pages": failed,
        },
    )

    return results
