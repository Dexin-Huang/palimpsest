from __future__ import annotations

from pathlib import Path

from palimpsest.transcription import PromptConfig, RunConfig, run_batch

from .download import download_pages
from .metadata import update_metadata


def run_document(
    *,
    doc_dir: Path,
    prompt_set: str = "transcription_json",
    pass_mode: str = "both",
    workers: int = 10,
    max_attempts: int = 3,
    delay: float = 2.0,
    auto_skip_non_text: bool = False,
    download_first: bool = True,
    pattern: str = "*.jpg",
) -> None:
    if download_first:
        download_pages(doc_dir=doc_dir, overwrite=False)

    update_metadata(doc_dir, {"status": "transcribing"})

    images_dir = doc_dir / "images"
    out_dir = doc_dir / "exports" / "transcriptions_full"
    prompt = PromptConfig(prompt_set=prompt_set)
    run_config = RunConfig(
        prompt=prompt,
        pass_mode=pass_mode,
        workers=workers,
        max_attempts=max_attempts,
        delay=delay,
        auto_skip_non_text=auto_skip_non_text,
    )
    results = run_batch(image_dir=images_dir, out_dir=out_dir, pattern=pattern, run_config=run_config)
    if pass_mode == "pass1":
        complete = sum(1 for r in results if r.get("status") in ("complete", "pass1_complete"))
    else:
        complete = sum(1 for r in results if r.get("status") == "complete")
    failed = len(results) - complete
    status = "assembled" if failed == 0 else "transcription_failed"
    update_metadata(doc_dir, {"status": status, "failed_pages": failed, "processed_pages": len(results)})
