#!/usr/bin/env python3
"""Minimal validator for Rescript page JSON (v1)."""
import argparse
import json
from pathlib import Path

REQUIRED_TOP = ['schema_version','page_id','doc_id','image','zones']
REQUIRED_IMAGE = ['path','width_px','height_px']

def fail(msg: str):
    raise SystemExit(f"[INVALID] {msg}")

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('--page', required=True)
    args = ap.parse_args()

    page = json.loads(Path(args.page).read_text(encoding='utf-8'))
    for k in REQUIRED_TOP:
        if k not in page:
            fail(f"missing top-level field: {k}")

    if page['schema_version'] != 'rescript.page.v1':
        fail(f"unexpected schema_version: {page['schema_version']}")
    for k in REQUIRED_IMAGE:
        if k not in page['image']:
            fail(f"missing image field: image.{k}")

    if not isinstance(page['zones'], list) or len(page['zones']) == 0:
        fail("zones must be a non-empty list")

    for i, z in enumerate(page['zones']):
        for k in ['zone_id','type','order','bbox_norm','text']:
            if k not in z:
                fail(f"zone[{i}] missing field: {k}")
        bb = z['bbox_norm']
        if not (isinstance(bb, list) and len(bb) == 4):
            fail(f"zone[{i}].bbox_norm must be [x,y,w,h]")
        if any((not isinstance(v, (int,float))) for v in bb):
            fail(f"zone[{i}].bbox_norm values must be numeric")
        if any((v < 0 or v > 1.5) for v in bb):
            fail(f"zone[{i}].bbox_norm appears out of range (expected 0..1)")
        if not isinstance(z['text'], dict):
            fail(f"zone[{i}].text must be an object")

    print("[OK]", page['page_id'])

if __name__ == '__main__':
    main()
