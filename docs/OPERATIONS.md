# Operations

This runbook covers the production factory only. Palimpsest surveys catalog
records, acquires IIIF manuscripts, runs each manuscript through a recipe, and
publishes EPUBs and content-addressed release bundles.

Research, comparative evaluation, and candidate promotion belong in the
separate `palimpsest-research` repository. A selected result enters this
repository as an explicit station, prompt, or recipe change.

## 1. Authorities

| Question | Authority |
|---|---|
| Which sources are known? | `library/catalog.db` |
| Which manuscripts are on the line? | `library/factory.db` |
| Which configuration does a manuscript run? | `palimpsest/factory/recipes/*.yaml` |
| Which artifacts exist? | `library/<doc_id>/` |
| Which catalog record did a workspace adopt? | `catalog_record_id` in `library/<doc_id>/metadata.json` (`null` = no catalog adoption) |
| Which artifact and station shapes are valid? | `docs/CONTRACTS.md` |
| Which books are released? | The immutable publication bundle in object storage |

`docs/CONTRACTS.md` is generated. Regenerate it after a contract or station
change:

```bash
python -m palimpsest graph --write-docs
```

## 2. Preflight

Use the repository virtual environment. Confirm package integrity and factory
state before paid work:

```bash
python -m pip check
python -m palimpsest doctor
python -m palimpsest status
```

`doctor` checks authoritative databases, terminal products, recipes, and
configuration freshness. A failure blocks production. A warning identifies
completed work whose recorded configuration differs from the selected recipe;
review that difference before an explicit refresh.

Model-backed stations require OMP and the provider logins named in `README.md`.
Copy `.env.example` to `.env` only when a local override is required. Recipe
model, prompt, parameter, and option values remain the production slot
authority.

## 3. Survey catalog records

Register and refresh a source head:

```bash
python -m palimpsest catalog init
python -m palimpsest catalog source add-gallica pelliot-chinois \
  --query 'dc.title all \"Pelliot chinois\"' \
  --collection \"Pelliot chinois\"
python -m palimpsest catalog sync pelliot-chinois
python -m palimpsest catalog stats
```

Survey a bounded, paid window of records:

```bash
python -m palimpsest survey run SOURCE_ID \
  --limit 12 \
  --pages 3 \
  --keep 5 \
  --max-cost 1
```

Each run samples page images from the current catalog window, asks the triage
model to describe what it read (neutral) and to guess the content, then answer
independent yes/no checks; the hit score is the number of true checks (0-5),
computed by us, and every decision is persisted as immutable evidence in
`library/survey.db` (`survey_evaluations`, `survey_runs`). The window position
is durable: the next run resumes after the last evaluated record,
already-surveyed records are never re-paid, and records whose manifest already
exists in the library are skipped. `--after SOURCE_KEY` overrides the cursor;
`--reset-cursor` starts the window over.

Inspect progress and the derived queue:

```bash
python -m palimpsest survey status SOURCE_ID
python -m palimpsest survey queue SOURCE_ID
```

`status` reports eligible, evaluated, hits, remaining, and last-run cost.
`queue` lists evaluations with hits > 0 that are not yet adopted by a
workspace `catalog_record_id`, hit score first, with the true checks, the
content guess, and the neutral what-was-read description.

Catalog presence and a model recommendation do not create a production work
order. An operator chooses either an active catalog record (by exact record
ID) or a direct IIIF manifest; the two source selectors are mutually
exclusive.

## 4. Intake or adopt

Create source contracts and a work order from an active catalog record
(catalog-backed) or directly from IIIF:

```bash
python -m palimpsest intake \
  --doc-id DOC_ID \
  --catalog-record-id source-record:SHA256 \
  --recipe RECIPE

python -m palimpsest intake \
  --doc-id DOC_ID \
  --manifest IIIF_MANIFEST_URL \
  --recipe RECIPE
```

`--catalog-record-id` and `--manifest` are mutually exclusive source
selectors. Catalog-backed intake resolves the exact record ID against the
current active catalog rows and derives the manifest URL from CatalogDB;
unknown, tombstoned, or manifest-less records fail before a workspace is
created. Direct manifest intake accepts an IIIF Presentation 2 or 3 URL
directly. Either path validates the recipe first, writes `metadata.json` and
`page_list.json` atomically, and creates the work order in the same command.

Workspace metadata records exactly one top-level `catalog_record_id`: the
exact `source-record:SHA256` of the adopted active row, or `null` for direct
manifest intake. Catalog adoption is never inferred from titles, shelfmarks,
ARKs, manifest or catalog URLs, or doc IDs.

Use `adopt` only when `library/<doc_id>/metadata.json` and `page_list.json`
already exist and validate:

```bash
python -m palimpsest adopt --doc-id DOC_ID --recipe RECIPE
```

Use `--switch` to change the selected recipe of an existing work order. Treat
that as a production configuration change; inspect the stale cells before
refreshing them.

## 5. Run the line

Run one work order:

```bash
python -m palimpsest run \
  --doc-id DOC_ID \
  --workers 6 \
  --model-workers 3
```

The conductor processes page-grain stations concurrently and crosses into
manuscript-grain stations only after all required page artifacts are complete.
It skips fresh cells, resumes interrupted work, and records every outcome in
the production ledger.

Run a bounded active queue:

```bash
python -m palimpsest run \
  --active \
  --limit 2 \
  --max-total-cost 1 \
  --workers 6 \
  --model-workers 3
```

The queue checks the work-order limit and cost ceiling between manuscripts.
Each manuscript still runs through its recipe in creation order.

Run a bounded page refresh:

```bash
python -m palimpsest run \
  --doc-id DOC_ID \
  --page PAGE_ID \
  --through read \
  --workers 1
```

`--page` is repeatable. A page-selected run cannot cross the first
manuscript-grain station.

Use subprocess cells when process isolation is required:

```bash
python -m palimpsest run --doc-id DOC_ID --executor subprocess
```

## 6. Configuration changes

The recipe is the production route sheet. Unknown station variants, parameters,
options, or broken artifact chains fail before execution.

After a station, prompt, model, parameter, or option changes, `doctor` and
`status --doc-id` expose the resulting drift. Paid cells do not rerun
implicitly. Refresh only the reviewed station:

```bash
python -m palimpsest run --doc-id DOC_ID --refresh STATION
```

The changed output fingerprint makes dependent downstream cells stale. Inspect
the refreshed result and recorded cost before continuing the work order.

## 7. Inspect, park, and resume

```bash
python -m palimpsest status --doc-id DOC_ID
```

Park a work order that must leave active operation:

```bash
python -m palimpsest park --doc-id DOC_ID
```

Parking preserves source records, artifacts, costs, and production history. A
later explicit `run --doc-id DOC_ID` resumes the work.

## 8. Products and publication

A complete work order has these terminal products:

```text
library/<doc_id>/book/book.json
library/<doc_id>/book/<doc_id>.epub
```

Books are produced under the current contract: Book `schema_version` 2 with
profile `facsimile-spread`, library bundles with profile `palimpsest-library`,
and bundles declaring `contract_version` 2.0.0 (canonical schemas
`book-object.schema.json` and `library-object.schema.json`). `publish` copies
`catalog_record_id` unchanged from workspace metadata into the top level of
each BookObject.

Rebuild the local static library:

```bash
python -m palimpsest site
```

Export a renderer-independent bundle:

```bash
python -m palimpsest export-library --output publication
```

Publish an immutable release:

```bash
python -m palimpsest publish \
  --bucket BUCKET \
  --profile PROFILE \
  --endpoint-url ENDPOINT_URL \
  --public-base-url PUBLIC_BASE_URL
```

Publication rebuilds the bundle, uploads it under its content-derived ID, and
verifies the complete remote inventory before success. Downstream consumers
import the printed `library.json` URL and verify every declared SHA-256 digest.

## 9. Snapshot and restore

Create the snapshot on off-host or removable storage:

```bash
python -m palimpsest snapshot create \
  --output OFF_HOST_PATH/palimpsest.zip
```

The command refuses active work and creates online SQLite backups. It includes
production workspaces and the shared content-addressed object store. It
excludes lock files and transient SQLite sidecars.

Verify the archive:

```bash
python -m palimpsest snapshot verify OFF_HOST_PATH/palimpsest.zip
```

Restore only into a new destination:

```bash
python -m palimpsest snapshot restore OFF_HOST_PATH/palimpsest.zip \
  --destination RESTORE_ROOT
```

Run `doctor` against the restored databases and library root before using the
restore for production.

## 10. Incident protocol

1. Stop new paid work.
2. Record the failing document, page, station, command, and observed error.
3. Preserve the workspace and ledger. Do not delete or rewrite prior evidence.
4. Repair the source defect.
5. Run the smallest bounded reproduction.
6. Inspect the output and cost.
7. Refresh only the affected production station.
8. Run `doctor` and verify terminal products before publication.

Absence is recoverable only when the code matches the exact missing-file
condition. Do not use broad exception handling around durable reads or writes.

## 11. Repository release

Before a release-affecting change:

```bash
python -m ruff check palimpsest tests
python -m pytest -q
python -m pip check
python -m pip wheel . --no-deps --wheel-dir dist-smoke
```

Install the wheel into a clean target and run `palimpsest --help`,
`palimpsest doctor`, and one non-paid command from outside the checkout. This
detects missing package data and stale build output.
