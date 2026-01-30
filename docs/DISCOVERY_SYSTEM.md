# Vatican Manuscript Discovery System

This module builds a master registry of potential manuscripts and flags
interesting candidates for processing.

## Core scripts

1) Crawl a shelfmark range and save inventory:
```
python -m palimpsest discovery crawl \
  --collection Pal.lat \
  --range 1200-1400 \
  --output discovery/registry/pal_lat_1200-1400_inventory.jsonl \
  --manifest-dir discovery/manifests \
  --limit 200
```

2) Append an inventory to the master list:
```
python -m palimpsest discovery master append \
  --input discovery/registry/pal_lat_1200-1400_inventory.jsonl
```

3) Cache manifests for records in the master list:
```
python -m palimpsest discovery master fetch-manifests
```

4) Filter interesting candidates (metadata-only pass):
```
python -m palimpsest discovery filter \
  --input discovery/registry/pal_lat_1200-1400_inventory.jsonl \
  --output discovery/registry/pal_lat_1200-1400_interesting.jsonl
```

## One-command crawl + sync

```
python -m palimpsest discovery run --collection Pal.lat --range 1200-1400 --limit 200 \
  --output discovery/registry/pal_lat_1200-1400_inventory.jsonl
```

## Registry format (JSONL)

Each line is a JSON record with:
- `manuscript_id`, `shelfmark`, `collection`
- `iiif.manifest_url`, `iiif.viewer_url`, `iiif.canvas_count`
- `content` (title, author, date, language)
- `discovery` (first_seen/last_seen)
- `status`

The master list lives at:
```
discovery/registry/master.jsonl
```

## Notes

- Manifests do not expose digitization dates. We track `first_seen_at` and
  `last_seen_at` in the master registry to approximate "newness."
- Full triage with Gemini is a separate step; use the prompt in
  `palimpsest/prompts/opportunity_triage.txt` as needed.
