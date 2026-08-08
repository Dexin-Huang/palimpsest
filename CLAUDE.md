# CLAUDE.md

Guidance for coding agents working in this repo.

## Project overview

Palimpsest is a provenance-first factory that turns manuscript images into
trustworthy, readable books. The current canonical path is:

Catalog sync -> Operator selection -> Intake (catalog record or manifest)
-> Run the recipe -> Inspect and publish.

## Core commands

Sync a source catalog head:

```
python -m palimpsest catalog init
python -m palimpsest catalog source add-gallica pelliot-chinois \
  --query 'dc.title all "Pelliot chinois"' \
  --collection "Pelliot chinois"
python -m palimpsest catalog sync pelliot-chinois
```

Sample catalog records and shortlist candidates (paid):

```
python -m palimpsest select pelliot-chinois --limit 12 --pages 3 --keep 5 --max-cost 1
```

Intake from an active catalog record (catalog-backed):

```
python -m palimpsest intake \
  --doc-id vatican_pal_lat_1267 \
  --catalog-record-id source-record:SHA256 \
  --recipe latin_manuscript
```

Intake directly from an IIIF manifest:

```
python -m palimpsest intake \
  --doc-id vatican_pal_lat_1267 \
  --manifest https://digi.vatlib.it/iiif/MSS_Pal.lat.1267/manifest.json \
  --recipe latin_manuscript
```

`--catalog-record-id` and `--manifest` are mutually exclusive source selectors.
Workspace metadata records the adopted record ID (or `null`) in the required
top-level `catalog_record_id`; never infer catalog adoption from titles,
shelfmarks, ARKs, URLs, or doc IDs.

Adopt an existing workspace and run the line:

```
python -m palimpsest adopt --doc-id vatican_pal_lat_1267 --recipe latin_manuscript
python -m palimpsest run --doc-id vatican_pal_lat_1267 --workers 6 --model-workers 3
```

Inspect and publish:

```
python -m palimpsest status --doc-id vatican_pal_lat_1267
python -m palimpsest site
python -m palimpsest publish --bucket BUCKET --profile PROFILE --endpoint-url ENDPOINT_URL --public-base-url PUBLIC_BASE_URL
```

Run `python -m palimpsest <command> --help` for command-specific options. The
canonical runbook is `docs/OPERATIONS.md`.

## Configuration

Use `.env` for model selection:

- `PALIMPSEST_MODEL_READING` (primary read model)
- `PALIMPSEST_MODEL_READING_SECONDARY` (independent secondary reader)
- `PALIMPSEST_MODEL_EDITORIAL` (survey, translate, reconstruct)
- `PALIMPSEST_MODEL_ADJUDICATOR` (dual-reader disagreement adjudication)

## Repo structure

```
library/           # portable source records and factory artifacts per document
palimpsest/        # core Python package (cli, catalog, factory, gateway)
tests/             # deterministic catalog and factory behavior tests
docs/              # architecture, operations, contracts, glyphs
publication/       # local build output for immutable releases (not source)
```
