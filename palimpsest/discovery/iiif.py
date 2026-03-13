from __future__ import annotations

import re
from typing import Any


def normalize_manifest_text(value: Any) -> str:
    """Flatten common IIIF text structures to a plain string."""
    if isinstance(value, dict):
        for vals in value.values():
            if vals:
                return vals[0] if isinstance(vals, list) else str(vals)
        return ""
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value) if value is not None else ""


def _same_catalog_token(left: str, right: str) -> bool:
    norm = lambda s: re.sub(r"[^a-z0-9]+", "", s.lower())
    return bool(left and right and norm(left) == norm(right))


def _count_canvases(manifest: dict) -> int:
    return len(extract_canvases(manifest))


def extract_manifest_summary(manifest: dict) -> dict:
    """Extract a conservative source-catalog summary from an IIIF manifest."""
    metadata_entries: list[dict[str, str]] = []
    metadata_map: dict[str, str] = {}

    for entry in manifest.get("metadata", []) or []:
        label = normalize_manifest_text(entry.get("label")).strip()
        value = normalize_manifest_text(entry.get("value")).strip()
        if not label:
            continue
        metadata_entries.append({"label": label, "value": value})
        key = re.sub(r"\s+", " ", label).strip().lower()
        if key and key not in metadata_map and value:
            metadata_map[key] = value

    label = normalize_manifest_text(manifest.get("label")).strip()
    description = normalize_manifest_text(manifest.get("description")).strip()
    shelfmark = metadata_map.get("shelfmark") or label
    title = metadata_map.get("title") or label
    title_is_shelfmark = _same_catalog_token(title, shelfmark)

    return {
        "label": label or None,
        "title": title or None,
        "title_is_shelfmark": title_is_shelfmark,
        "shelfmark": shelfmark or None,
        "author": metadata_map.get("author") or metadata_map.get("creator"),
        "contributor": metadata_map.get("contributor") or metadata_map.get("other name"),
        "date": metadata_map.get("date") or metadata_map.get("dat"),
        "language": metadata_map.get("language") or metadata_map.get("lingua"),
        "place": metadata_map.get("place") or metadata_map.get("origin"),
        "description": description or None,
        "metadata_field_count": len(metadata_entries),
        "metadata_entries": metadata_entries,
        "canvas_count": _count_canvases(manifest),
    }


def extract_metadata(manifest: dict) -> dict:
    """Backward-compatible alias for discovery's manifest metadata shape."""
    return extract_manifest_summary(manifest)


def _extract_canvases_v2(manifest: dict) -> list[dict]:
    canvases: list[dict] = []
    for sequence in manifest.get("sequences", []):
        for canvas in sequence.get("canvases", []):
            for image in canvas.get("images", []):
                resource = image.get("resource", {})
                service = resource.get("service", {})
                image_id = service.get("@id", "") or resource.get("@id", "")
                if image_id:
                    canvases.append(
                        {
                            "id": canvas.get("@id", ""),
                            "label": canvas.get("label", ""),
                            "image_service": image_id,
                            "width": resource.get("width", canvas.get("width")),
                            "height": resource.get("height", canvas.get("height")),
                        }
                    )
                    break
    return canvases


def _extract_canvases_v3(manifest: dict) -> list[dict]:
    canvases: list[dict] = []
    for item in manifest.get("items", []):
        if item.get("type") != "Canvas":
            continue
        for anno_page in item.get("items", []):
            for anno in anno_page.get("items", []):
                body = anno.get("body", {})
                if isinstance(body, list):
                    body = body[0] if body else {}
                service = body.get("service", [])
                if isinstance(service, list) and service:
                    service = service[0]
                image_id = ""
                if isinstance(service, dict):
                    image_id = service.get("id", service.get("@id", ""))
                if not image_id:
                    image_id = body.get("id", "")
                if image_id:
                    label = item.get("label", {})
                    if isinstance(label, dict):
                        for vals in label.values():
                            if vals:
                                label = vals[0] if isinstance(vals, list) else vals
                                break
                        else:
                            label = ""
                    canvases.append(
                        {
                            "id": item.get("id", ""),
                            "label": label,
                            "image_service": image_id,
                            "width": item.get("width"),
                            "height": item.get("height"),
                        }
                    )
                    break
    return canvases


def extract_canvases(manifest: dict) -> list[dict]:
    context = manifest.get("@context", "")
    if isinstance(context, list):
        context = " ".join(str(c) for c in context)
    if "iiif.io/api/presentation/3" in str(context) or "items" in manifest:
        return _extract_canvases_v3(manifest)
    return _extract_canvases_v2(manifest)
