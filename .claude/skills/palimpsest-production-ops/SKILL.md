---
name: palimpsest-production-ops
description: Operate and recover the Palimpsest manuscript production line. Use this skill whenever the user asks to intake or adopt a manuscript, run or resume a work order, inspect status, refresh a changed station, rebuild the site, diagnose failed or stale cells, locate book/EPUB outputs, publish a release, or handle a production incident.
---

# Palimpsest Production Operations

Use this skill only for real Palimpsest manuscript operations. Production owns
work orders, station freshness, the ledger, artifacts, books, EPUBs, and the
static library. It does not compare challengers.

## Read First

- `docs/OPERATIONS.md` for the affected production protocol
- `docs/FACTORY.md` only for the affected runtime invariant
- `docs/CONTRACTS.md` for the affected artifact path and station socket
- the selected recipe under `palimpsest/factory/recipes/`

Inspect the exact work order and status before changing anything. Do not infer a
document ID, recipe, source manifest, or refresh target when the ledger and
workspace can establish it.

## Classify the Operation

Choose one:

1. **Intake**: create canonical source records and a work order from an active
   catalog record or direct IIIF manifest.
2. **Adopt**: register an existing workspace whose canonical source records
   already exist.
3. **Run/resume**: let the conductor execute missing or input-stale cells and
   skip fresh cells.
4. **Intentional refresh**: explicitly rerun an authorized outdated station.
5. **Inspect/publish**: inspect state, book, EPUB, and rebuild the static site.
6. **Incident recovery**: preserve failure evidence, diagnose the boundary, and
   retry or refresh only after the cause is corrected.

Model, prompt, parameter, option, or implementation comparisons belong in the
separate `palimpsest-research` repository. This skill operates only the
selected production configuration already installed in a recipe.

## Source Boundary

Intake accepts exactly one source selector. For an active catalog record
(catalog-backed; the manifest is derived from CatalogDB by exact record ID):

```text
python -m palimpsest intake --doc-id DOC_ID --catalog-record-id source-record:SHA256 --recipe RECIPE
```

For a direct IIIF manifest:

```text
python -m palimpsest intake --doc-id DOC_ID --manifest MANIFEST_URL --recipe RECIPE
```

`--catalog-record-id` and `--manifest` are mutually exclusive. Catalog-backed
intake fails on unknown, tombstoned, or manifest-less records before creating
a workspace. Direct manifest intake records `catalog_record_id: null`. Never
infer catalog adoption from titles, shelfmarks, ARKs, URLs, or doc IDs.

For an existing workspace with canonical `metadata.json` and `page_list.json`:

```text
python -m palimpsest adopt --doc-id DOC_ID --recipe RECIPE
```

Do not use adoption to bless derived artifacts or use intake to overwrite
canonical source records. If source identity is wrong, correct the source
boundary explicitly.

## Run and Freshness

Standard execution:

```text
python -m palimpsest run --doc-id DOC_ID --workers N
```

Interpret states exactly:

- missing -> run;
- fresh -> skip;
- upstream input drift -> run stale cell;
- configuration drift -> report outdated, do not silently repeat paid work;
- failed -> preserve structured failure and diagnose before retry.

Use `--executor subprocess` when process isolation is required. Executor choice
must not alter artifact contracts.

## Explicit Refresh

After an authorized recipe, prompt, model, option, or implementation change:

```text
python -m palimpsest run --doc-id DOC_ID --refresh STATION
```

Refresh is the auditable paid-work control. Never delete output, provenance, or
ledger rows to make a cell appear missing or stale. Refresh only the intended
station; let artifact fingerprints propagate downstream staleness.

Confirm that the recipe change is the selected production configuration before
refreshing. The factory records identity and drift; it does not judge quality.

## Inspect Terminal Product

```text
python -m palimpsest status --doc-id DOC_ID
python -m palimpsest site
```

Verify the required outputs:

```text
library/<doc_id>/book/book.json
library/<doc_id>/book/<doc_id>.epub
site/index.html
```

A successful command is not sufficient when the requested operation includes a
readable product. Inspect book completeness, EPUB validity, reader output, and
source evidence links relevant to the change.

## Incident Protocol

1. Stop further dispatch when continued work risks corruption or waste.
2. Preserve the artifact, provenance, ledger state, stderr, recipe, and model
   identity.
3. Classify the fault: source, contract, station, provider, executor, or
   presentation.
4. Fix the cause; never patch freshness or completion state.
5. Run the smallest deterministic reproduction and focused tests.
6. Apply explicit refresh only to intended manuscripts.
7. Reinspect status and terminal output.
8. Revert the reviewed recipe or implementation change if the new production
   configuration must be withdrawn.

Never hand-edit `library/factory.db`, provenance stamps, or generated artifacts.
Unknown cost or usage remains unknown.

## Contract or Graph Change

When an operation reveals a real artifact or station socket change, stop
production repair and follow `palimpsest-station-development`. Regenerate the
contract graph only from live registries:

```text
python -m palimpsest graph --write-docs
```

Never edit `docs/CONTRACTS.md` directly.

## Verification

Choose focused proof from the changed boundary:

- intake/adopt: canonical source record and ledger behavior;
- conductor/freshness: affected conductor tests and a work-order smoke path;
- station behavior: focused station tests and a bounded production smoke path;
- publication/site: book, EPUB, reader, and evidence-link checks;
- package/release: full tests, lint, dependency check, and built-wheel smoke.

## Output Format

Report:

```text
Operation:
Document and recipe:
Pre-operation state:
Commands executed:
Cells run/skipped/outdated/failed:
Cost and unknown-cost state:
Artifacts produced or preserved:
Book/EPUB/site checks:
Incident cause, if any:
Explicit refresh or configuration revert used:
Remaining operator action:
```

Do not claim completion without observing the requested terminal state.
