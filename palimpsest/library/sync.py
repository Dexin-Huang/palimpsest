from __future__ import annotations

import json
from pathlib import Path

from .io import read_json


def sync_master_for_doc(doc_dir: Path, master_path: Path) -> bool:
    if not master_path.exists():
        return False
    metadata = read_json(doc_dir / "metadata.json")
    page_list = read_json(doc_dir / "page_list.json")
    manifest_url = page_list.get("manifest_url")
    if not manifest_url:
        return False

    records = []
    updated = False
    for line in master_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError:
            continue
        iiif = record.get("iiif") or {}
        if iiif.get("manifest_url") == manifest_url:
            record["processing"] = {
                "doc_id": metadata.get("doc_id"),
                "status": metadata.get("status"),
                "updated_at": metadata.get("updated_at"),
            }
            updated = True
        records.append(record)

    if updated:
        with master_path.open("w", encoding="utf-8") as handle:
            for record in records:
                handle.write(json.dumps(record, ensure_ascii=True))
                handle.write("\n")
    return updated
