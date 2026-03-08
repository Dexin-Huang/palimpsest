from __future__ import annotations

from pathlib import Path

from palimpsest.transcription import PromptConfig, RunConfig, run_batch

from .download import download_pages
from .io import read_json
from .metadata import update_metadata
from .sync import sync_master_for_doc


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
    master_path: Path | None = None,
    use_cleaned: bool | None = None,
    use_cache: bool = True,
) -> None:
    """Run transcription pipeline on a document.

    Args:
        use_cleaned: Use cleaned images instead of originals.
            - None (default): Auto-detect (use cleaned if images_cleaned/ exists and has files)
            - True: Force use of cleaned images (error if not found)
            - False: Force use of original images
    """
    if download_first:
        download_pages(doc_dir=doc_dir, overwrite=False)

    update_metadata(doc_dir, {"status": "transcribing"})

    # Choose image source
    cleaned_dir = doc_dir / "images_cleaned"
    original_dir = doc_dir / "images"

    if use_cleaned is None:
        # Auto-detect: use cleaned if directory exists and has matching files
        if cleaned_dir.exists() and list(cleaned_dir.glob(pattern)):
            images_dir = cleaned_dir
        else:
            images_dir = original_dir
    elif use_cleaned:
        # Force cleaned
        if not cleaned_dir.exists() or not list(cleaned_dir.glob(pattern)):
            raise ValueError(
                f"Cleaned images not found: {cleaned_dir}. Run 'library clean' first."
            )
        images_dir = cleaned_dir
    else:
        # Force original
        images_dir = original_dir
    out_dir = doc_dir / "exports" / "transcriptions_full"
    prompt = PromptConfig(prompt_set=prompt_set)
    run_config = RunConfig(
        prompt=prompt,
        pass_mode=pass_mode,
        workers=workers,
        max_attempts=max_attempts,
        delay=delay,
        auto_skip_non_text=auto_skip_non_text,
        use_cache=use_cache,
    )
    try:
        results = run_batch(image_dir=images_dir, out_dir=out_dir, pattern=pattern, run_config=run_config)
    except Exception as exc:
        update_metadata(doc_dir, {"status": "transcription_failed", "error": str(exc)})
        raise

    if not results:
        update_metadata(doc_dir, {"status": "no_pages", "failed_pages": 0, "processed_pages": 0})
        return

    if pass_mode == "pass1":
        complete = sum(1 for r in results if r.get("status") in ("complete", "pass1_complete"))
    else:
        complete = sum(1 for r in results if r.get("status") == "complete")
    failed = len(results) - complete

    status = "transcription_failed" if failed else ("pass1_complete" if pass_mode == "pass1" else "assembled")
    updates = {"status": status, "failed_pages": failed, "processed_pages": len(results)}

    if status == "assembled":
        canonical_manifest = doc_dir / "exports" / "canonical_pages" / "canonical_manifest.json"
        if canonical_manifest.exists():
            canonical_index = read_json(canonical_manifest)
            updates["canonical_pages_count"] = canonical_index.get("total_pages", 0)
        else:
            updates["status"] = "assembled_missing"
        restoration_manifest = doc_dir / "exports" / "restoration" / "restoration_manifest.json"
        if restoration_manifest.exists():
            restoration_index = read_json(restoration_manifest)
            updates["restored_pages"] = restoration_index.get("total_pages", 0)
        else:
            updates["status"] = "assembled_missing"

    update_metadata(doc_dir, updates)
    if master_path:
        sync_master_for_doc(doc_dir, master_path)
