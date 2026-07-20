"""IIIF intake: one manifest becomes one factory work order.

Intake owns the boundary between an external archive and the factory. Parsing is
pure; network access and atomic workspace writes are kept at the edge. The line
itself consumes only the resulting ``metadata`` and ``page_list`` contracts.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any, Mapping

import requests

from palimpsest.factory.core.contracts import validate_payload
from palimpsest.factory.workspace.io import atomic_write_json, utc_now
from palimpsest.factory.workspace.layout import metadata_path, page_list_path

DOC_ID_RE = re.compile(r"^[a-z0-9]+(?:_[a-z0-9]+)*$")
REQUEST_HEADERS = {"User-Agent": "palimpsest manuscript recovery factory"}
TIMEOUT_SECONDS = 30.0


def fetch_manifest(url: str) -> dict[str, Any]:
    response = requests.get(url, timeout=TIMEOUT_SECONDS, headers=REQUEST_HEADERS)
    response.raise_for_status()
    manifest = response.json()
    if not isinstance(manifest, dict):
        raise ValueError("IIIF manifest must be a JSON object")
    return manifest


def build_records(
    doc_id: str,
    manifest_url: str,
    manifest: Mapping[str, Any],
    *,
    image_size: int | str = "max",
) -> tuple[dict[str, Any], dict[str, Any]]:
    """Convert a IIIF Presentation 2 or 3 manifest into source contracts."""
    if not DOC_ID_RE.fullmatch(doc_id):
        raise ValueError(
            "doc_id must contain lowercase ASCII letters, digits, and single underscores"
        )

    canvases = _canvases(manifest)
    if not canvases:
        raise ValueError("IIIF manifest contains no readable image canvases")

    catalog = _catalog(manifest, canvas_count=len(canvases))
    pages: list[dict[str, Any]] = []
    used_ids: set[str] = set()
    for index, canvas in enumerate(canvases):
        page_id = _page_id(index, canvas.get("label"), used_ids)
        used_ids.add(page_id)
        pages.append(
            {
                "page_id": page_id,
                "canvas_id": canvas.get("canvas_id", ""),
                "url": _image_url(str(canvas["image_service"]), image_size),
                "order": index + 1,
                "width": canvas.get("width"),
                "height": canvas.get("height"),
                "label": _text(canvas.get("label")),
            }
        )

    now = utc_now()
    metadata = {
        "doc_id": doc_id,
        "source": {"kind": "iiif", "manifest_url": manifest_url},
        "source_catalog": catalog,
        "created_at": now,
        "updated_at": now,
    }
    page_list = {
        "doc_id": doc_id,
        "manifest_url": manifest_url,
        "image_size": image_size,
        "pages": pages,
    }
    validate_payload("metadata", metadata)
    validate_payload("page_list", page_list)
    return metadata, page_list


def write_records(
    doc_id: str,
    metadata: Mapping[str, Any],
    page_list: Mapping[str, Any],
    *,
    library_root: Path,
) -> Path:
    """Atomically install intake records without touching line artifacts."""
    validate_payload("metadata", metadata)
    validate_payload("page_list", page_list)
    atomic_write_json(metadata_path(doc_id, library_root), metadata)
    atomic_write_json(page_list_path(doc_id, library_root), page_list)
    return metadata_path(doc_id, library_root).parent


def _text(value: Any) -> str:
    if isinstance(value, Mapping):
        for candidate in value.values():
            if isinstance(candidate, list) and candidate:
                return str(candidate[0])
            if candidate:
                return str(candidate)
        return ""
    if isinstance(value, list):
        return str(value[0]) if value else ""
    return str(value) if value is not None else ""


def _catalog(manifest: Mapping[str, Any], *, canvas_count: int) -> dict[str, Any]:
    entries: list[dict[str, str]] = []
    fields: dict[str, str] = {}
    for entry in manifest.get("metadata", []) or []:
        label = _text(entry.get("label")).strip()
        value = _text(entry.get("value")).strip()
        if not label:
            continue
        entries.append({"label": label, "value": value})
        fields.setdefault(re.sub(r"\s+", " ", label).lower(), value)

    label = _text(manifest.get("label")).strip()
    title = fields.get("title") or label
    shelfmark = fields.get("shelfmark") or label
    return {
        "label": label or None,
        "title": title or None,
        "shelfmark": shelfmark or None,
        "author": fields.get("author") or fields.get("creator") or None,
        "date": fields.get("date") or fields.get("dat") or None,
        "language": fields.get("language") or fields.get("lingua") or None,
        "description": _text(manifest.get("description")).strip() or None,
        "canvas_count": canvas_count,
        "metadata_entries": entries,
    }


def _canvases(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    if manifest.get("items") is not None:
        return _canvases_v3(manifest)
    return _canvases_v2(manifest)


def _canvases_v2(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    canvases: list[dict[str, Any]] = []
    for sequence in manifest.get("sequences", []) or []:
        for canvas in sequence.get("canvases", []) or []:
            for annotation in canvas.get("images", []) or []:
                resource = annotation.get("resource") or {}
                service = resource.get("service") or {}
                if isinstance(service, list):
                    service = service[0] if service else {}
                image_service = (
                    (
                        service.get("@id") or service.get("id")
                        if isinstance(service, Mapping)
                        else ""
                    )
                    or resource.get("@id")
                    or resource.get("id")
                )
                if image_service:
                    canvases.append(
                        {
                            "canvas_id": canvas.get("@id", ""),
                            "label": canvas.get("label", ""),
                            "image_service": image_service,
                            "width": resource.get("width", canvas.get("width")),
                            "height": resource.get("height", canvas.get("height")),
                        }
                    )
                    break
    return canvases


def _canvases_v3(manifest: Mapping[str, Any]) -> list[dict[str, Any]]:
    canvases: list[dict[str, Any]] = []
    for canvas in manifest.get("items", []) or []:
        if canvas.get("type") != "Canvas":
            continue
        found = False
        for annotation_page in canvas.get("items", []) or []:
            for annotation in annotation_page.get("items", []) or []:
                body = annotation.get("body") or {}
                if isinstance(body, list):
                    body = body[0] if body else {}
                services = body.get("service") or []
                if isinstance(services, Mapping):
                    services = [services]
                service = services[0] if services else {}
                image_service = (
                    service.get("id")
                    or service.get("@id")
                    or body.get("id")
                    or body.get("@id")
                )
                if image_service:
                    canvases.append(
                        {
                            "canvas_id": canvas.get("id", ""),
                            "label": canvas.get("label", ""),
                            "image_service": image_service,
                            "width": body.get("width", canvas.get("width")),
                            "height": body.get("height", canvas.get("height")),
                        }
                    )
                    found = True
                    break
            if found:
                break
    return canvases


def _image_url(service_url: str, size: int | str) -> str:
    base = service_url.rstrip("/")
    if re.search(r"\.(?:jpe?g|png|webp|tiff?)$", base, re.IGNORECASE):
        return base
    size_parameter = "max" if str(size).lower() in {"max", "full"} else f"{int(size)},"
    if "/full/" in base:
        return re.sub(r"/full/[^/]+/", f"/full/{size_parameter}/", base)
    return f"{base}/full/{size_parameter}/0/default.jpg"


def _page_id(index: int, label: Any, used: set[str]) -> str:
    match = re.search(r"(?:f(?:ol(?:io)?)?\.?\s*)?(\d+)\s*([rv])?", _text(label), re.I)
    candidate = (
        f"f{int(match.group(1)):03d}{(match.group(2) or 'r').lower()}"
        if match
        else f"page_{index:04d}"
    )
    if candidate not in used:
        return candidate
    return f"page_{index:04d}"
