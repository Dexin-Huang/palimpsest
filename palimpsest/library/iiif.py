from __future__ import annotations

import re
from typing import Any

import requests


def fetch_manifest(url: str) -> dict:
    resp = requests.get(url, timeout=30)
    resp.raise_for_status()
    return resp.json()


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


def build_image_url(service_url: str, size: int | str) -> str:
    base = service_url.rstrip("/")
    if isinstance(size, str) and size.lower() in ("max", "full"):
        size_param = "max"
    else:
        size_param = f"{size},"
    if "/full/" in base:
        return re.sub(r"/full/[^/]+/", f"/full/{size_param}/", base)
    return f"{base}/full/{size_param}/0/default.jpg"


def derive_filename(index: int, label: Any) -> str:
    if label:
        m = re.search(r"(?:f(?:ol(?:io)?)?\.?\s*)?(\d+)\s*([rv])?", str(label), re.I)
        if m:
            num = int(m.group(1))
            side = (m.group(2) or "r").lower()
            return f"f{num:03d}{side}.jpg"
    return f"page_{index:04d}.jpg"


def build_page_list(manifest_url: str, size: int | str = "max") -> dict:
    manifest = fetch_manifest(manifest_url)
    canvases = extract_canvases(manifest)
    if not canvases:
        raise ValueError("No canvases found in manifest")
    pages: list[dict] = []
    used: set[str] = set()
    for idx, canvas in enumerate(canvases):
        filename = derive_filename(idx, canvas.get("label", ""))
        if filename in used:
            filename = f"page_{idx:04d}.jpg"
        counter = 1
        while filename in used:
            stem = filename.replace(".jpg", "")
            filename = f"{stem}_{counter}.jpg"
            counter += 1
        used.add(filename)
        pages.append(
            {
                "page_id": filename.replace(".jpg", ""),
                "url": build_image_url(canvas["image_service"], size),
                "filename": filename,
                "order": idx + 1,
                "width": canvas.get("width"),
                "height": canvas.get("height"),
                "label": canvas.get("label", ""),
            }
        )
    return {
        "manifest_url": manifest_url,
        "image_size": size,
        "pages": pages,
    }
