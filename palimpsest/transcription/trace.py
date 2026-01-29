import json
from datetime import datetime
from pathlib import Path
from typing import Optional

from .io import atomic_write_bytes, atomic_write_text


def save_trace(
    trace_dir: Path,
    page_id: str,
    stage: str,
    agentic_response,
    response,
    model: str,
    prompt_hash: str,
    extra: Optional[dict] = None,
) -> Path:
    trace_dir.mkdir(parents=True, exist_ok=True)
    image_files = []
    for idx, image in enumerate(agentic_response.images, 1):
        mime = image.get("mime_type", "image/jpeg")
        ext = "jpg"
        if "png" in mime:
            ext = "png"
        filename = f"{page_id}_{stage}_image_{idx:02d}.{ext}"
        out_path = trace_dir / filename
        atomic_write_bytes(out_path, image.get("data", b""))
        image_files.append(filename)

    usage = None
    if hasattr(response, "usage_metadata") and response.usage_metadata:
        usage = {
            "prompt_token_count": getattr(response.usage_metadata, "prompt_token_count", None),
            "candidates_token_count": getattr(response.usage_metadata, "candidates_token_count", None),
            "total_token_count": getattr(response.usage_metadata, "total_token_count", None),
            "thoughts_token_count": getattr(response.usage_metadata, "thoughts_token_count", None),
        }

    trace = {
        "page_id": page_id,
        "stage": stage,
        "model": model,
        "prompt_hash": prompt_hash,
        "generated_at": datetime.utcnow().replace(microsecond=0).isoformat() + "Z",
        "text": agentic_response.text or "",
        "code_blocks": agentic_response.code_blocks,
        "code_results": agentic_response.code_results,
        "images": image_files,
        "usage": usage,
        "candidate_count": len(getattr(response, "candidates", []) or []),
    }
    if extra:
        trace.update(extra)

    trace_path = trace_dir / f"{page_id}_{stage}_trace.json"
    atomic_write_text(trace_path, json.dumps(trace, indent=2))
    return trace_path
