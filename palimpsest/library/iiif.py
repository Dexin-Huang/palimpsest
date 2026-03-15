from __future__ import annotations

import re
from typing import Any

import requests

from palimpsest.discovery.iiif import extract_canvases, extract_manifest_summary

REQUEST_HEADERS = {
    "User-Agent": "Mozilla/5.0 (Palimpsest library/IIIF)",
}


def fetch_manifest(url: str) -> dict:
    resp = requests.get(url, timeout=30, headers=REQUEST_HEADERS)
    resp.raise_for_status()
    return resp.json()


def build_image_url(service_url: str, size: int | str) -> str:
    base = service_url.rstrip("/")
    if re.search(r"\.(?:jpg|jpeg|png|webp|tif|tiff)$", base, re.I):
        return base
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
    manifest_summary = extract_manifest_summary(manifest)
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
        "manifest_label": manifest_summary.get("label"),
        "manifest_summary": manifest_summary,
        "image_size": size,
        "pages": pages,
    }
