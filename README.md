# Palimpsest

Palimpsest turns manuscript images into trustworthy, readable books.

It is a provenance-first production factory: an IIIF manifest enters as a work
order; a recipe routes every page through explicit stations; the final book
model renders to EPUB and a static library. Every transformation declares its
inputs and output, every artifact is written atomically, and every model call
is fingerprinted and costed.

## The line

```text
IIIF manifest
    │
    ▼
metadata + page_list
    │
    ├─ page line, parallel per page
    │  acquire → deframe → dewatermark → flatten → segment → dual read
    │          → align (recipe-dependent) → translate → assemble_page
    │
    └─ manuscript line
       survey → reconstruct → reference → emend → publish → render_epub
```

The page line preserves the evidence layer: source image, cleaned image,
diplomatic transcription, translation, and their provenance remain separate.
The manuscript line reconstructs continuity, checks received readings, records
editorial changes in an apparatus, and publishes without mutating the
diplomatic transcription.

## Install

Palimpsest requires Python 3.11 or newer. Install it in a repository-local
virtual environment so unrelated user packages cannot affect the factory.

Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade --editable ".[dev]"
```

macOS or Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade --editable ".[dev]"
```

The remaining commands assume that environment is active. Verify it after
installation with `python -m pip check`.

Factory model selectors containing `/` execute through OMP. The production
reading lane uses `openai-codex/gpt-5.6-sol` (low thinking) as its primary,
`google/gemini-3.6-flash` (no thinking argument) as its secondary, and
`anthropic/claude-fable-5` (high thinking) as its adjudicator. Ensure
`omp` is on `PATH`, start one interactive OMP session, and run
`/login openai-codex` and `/login anthropic` before the first run. Configure
OMP's Google provider as well; its Google backend accepts `GEMINI_API_KEY`
from the environment. OMP owns provider routing and OpenAI OAuth refresh for
selectors, so no OpenAI API key is required.

Copy `.env.example` to `.env` to use or override that lane:

```env
PALIMPSEST_MODEL_READING=openai-codex/gpt-5.6-sol
PALIMPSEST_MODEL_READING_SECONDARY=google/gemini-3.6-flash
PALIMPSEST_MODEL_ADJUDICATOR=anthropic/claude-fable-5
```

`PALIMPSEST_MODEL_READING` selects the model for the `read`, `survey`,
`translate`, and `reconstruct` stations. The secondary and adjudicator settings
apply to dual-reader `read` adjudication.

When migrating from `PALIMPSEST_MODEL_VISION`, rename it to
`PALIMPSEST_MODEL_READING`. The legacy name is rejected when the new setting is
absent; it is not accepted as an alias.

`GEMINI_API_KEY` may be supplied for OMP's Google provider and is also used by
the optional direct-provider override: set a model value to a bare `gemini...`
selector to bypass OMP. Slash-qualified selectors always go through OMP. The
`reference` and `emend` stations use the agent executor selected in each recipe
and require that executor's CLI on `PATH`.

## Run a manuscript

### 1. Intake from IIIF

Intake validates a IIIF Presentation 2 or 3 manifest, writes the two source
contracts, and creates the work order in the ledger:

```bash
python -m palimpsest intake \
  --doc-id vatican_pal_lat_1267 \
  --manifest https://digi.vatlib.it/iiif/MSS_Pal.lat.1267/manifest.json \
  --recipe latin_manuscript
```

For a document that already has `library/<doc_id>/metadata.json` and
`page_list.json`, adopt it without rewriting either source record:

```bash
python -m palimpsest adopt \
  --doc-id vatican_pal_lat_1267 \
  --recipe latin_manuscript
```

### 2. Run the line

```bash
python -m palimpsest run --doc-id vatican_pal_lat_1267 --workers 6
```

Each read cell sends both readers the same full-page or tile image and prompt.
Exact agreement after sanitization becomes the final text without another
model call. A disagreement sends the same image and identity-blind candidate
texts to the adjudicator under a strict JSON schema. The transcription artifact
retains the final text, both candidate readings and their model IDs, the
adjudication status, reasoning and unresolved items, plus combined token and
cost usage across every call made for that reading.

The conductor resumes from `library/factory.db`. Fresh cells are skipped;
input drift reruns stale cells; configuration drift is reported as outdated
without silently repeating paid work. Refresh a changed station explicitly:

```bash
python -m palimpsest run \
  --doc-id vatican_pal_lat_1267 \
  --refresh read
```

Bound a canary to selected pages and an inclusive station:

```bash
python -m palimpsest run \
  --doc-id gallica_pelliot_chinois_5579 \
  --page page_0001 \
  --page page_0058 \
  --through read \
  --workers 1
```

`--page` is repeatable. A page-selected run cannot cross the recipe's first
manuscript-grain station, so incomplete page evidence never feeds a manuscript
artifact. Successful partial runs leave the work order active; a later full run
reuses their fresh cells.

Use `--executor subprocess` to isolate each cell in a fresh Python process.
The station contract and artifacts are identical under either executor.

### 3. Inspect and publish

```bash
python -m palimpsest status --doc-id vatican_pal_lat_1267
python -m palimpsest site
```

Terminal outputs:

```text
library/<doc_id>/book/book.json
library/<doc_id>/book/<doc_id>.epub
site/index.html
```

## Harvest source catalogs

Catalog heads translate repository conventions at the boundary and write
source-local records to `library/catalog.db`; they do not create factory work
orders or merge records that may describe the same manuscript.

```bash
python -m palimpsest catalog init
python -m palimpsest catalog source add-gallica pelliot-chinois \
  --query 'dc.title all "Pelliot chinois"' \
  --collection "Pelliot chinois"
python -m palimpsest catalog sync pelliot-chinois
python -m palimpsest catalog stats
python -m palimpsest catalog records pelliot-chinois --limit 20
```

`normalized-jsonl` is the protocol-neutral import head. Every input line is an
envelope with `source_key`, strict canonical `record`, optional `source_url`
and `source_modified_at`, and the untouched `raw` source payload. Syncs are
resumable, unchanged records do not create revisions, changed records do, and
records absent from a completed refresh are tombstoned rather than deleted.

## Commands

| Command | Purpose |
|---|---|
| `init-db` | Initialize the SQLite inventory and production ledger |
| `catalog` | Register source heads, normalize records, and refresh the pointer catalog |
| `intake` | Turn an IIIF manifest into source contracts and a work order |
| `adopt` | Put an existing library workspace on the line |
| `run` | Execute or resume a recipe |
| `status` | Show work orders or completed station runs |
| `graph` | Render the live artifact/station contract graph |
| `preview` | Render preprocessing stages and segmentation lassos |
| `tune` | Tune segmentation offline without network or ledger writes |
| `site` | Rebuild the static library from published book models |
| `bench` | Verify, run, report, canary, promote, and roll back immutable evaluations |

Run `python -m palimpsest <command> --help` for command-specific options.

## Operating protocols

[`docs/OPERATIONS.md`](docs/OPERATIONS.md) is the canonical runbook for
day-to-day work: manuscript operations, new experiments, candidate and suite
versioning, interrupted-run recovery, promotion, production refresh, rollback,
benchmark governance, and release verification. Use it when deciding what
record to create and which gate comes next; use
[`docs/EVALUATION.md`](docs/EVALUATION.md) for the underlying evaluation
contracts.

Repository-aware coding sessions should use the source-controlled project
skills under `.claude/skills/`:

| Skill | Use it for |
|---|---|
| `palimpsest-experiment` | Design, initialize, run, resume, or review one bounded station experiment |
| `palimpsest-production-ops` | Intake, adopt, run, refresh, recover, publish, and inspect manuscripts |
| `palimpsest-promotion` | Qualification, proposal, canary, promotion, explicit production refresh, and rollback |
| `palimpsest-station-development` | Station variants, new transformations, artifact contracts, and implementation fingerprints |

The skills encode procedure and safety boundaries; the runbook and live
contracts remain the sources of truth. Invoke a skill explicitly when intent
could span experiment, production, and promotion boundaries.

## Swappable seams

### Recipes

A recipe is the route sheet. It chooses station order, models, prompts,
generation parameters, and station options without changing conductor code.
The repository currently ships:

- `latin_manuscript`
- `chinese_scroll`

Recipe keys are strict. Unknown parameters or station options fail during load,
before a network request or paid model call.

### Stations

Every station has one contract. Its logical name and variant identify the
implementation to execute; its localized implementation fingerprint, declared
inputs, optional inputs, and production dependencies determine freshness.
Stations emit exactly one `StationResult`:

```python
name: str
variant: str
implementation_fingerprint: str
grain: "page" | "manuscript"
consumes: tuple[str, ...]
produces: str
run(job) -> StationResult
```

Stations do not call one another, touch the ledger, or choose execution order.
A station reads only its fingerprinted inputs and writes one output. Adding a
new transformation means registering a station and composing it in a recipe;
the conductor remains unchanged.

### Model gateway

Model-backed stations submit provider-neutral `ModelRequest` values and receive
`ModelResponse` values carrying text, token usage, and cost. Provider-specific
SDK behavior, retries, response parsing, and pricing stay behind the gateway.

### Executors

The conductor sends a complete `CellSpec` to an executor. `inline` and
`subprocess` are interchangeable execution policies; neither owns scheduling,
freshness, or the ledger. Agentic editorial stations have a second contained
executor seam for `codex` and `omp`.

### Evaluation and promotion

The evaluation plane runs outside the production conductor. Immutable
candidates and suites drive paired, isolated executions through the same cell
and artifact contracts as production. Scorecards retain quality, hard-limit,
downstream, reliability, latency, cost, and blinded-judge evidence. A qualified
report can produce a compare-and-swap recipe proposal; a protected production
canary gates promotion, and append-only records support exact rollback.
[`docs/EVALUATION.md`](docs/EVALUATION.md) defines the contracts and
`palimpsest bench --help` exposes the operator workflow.

The checked-in suites exercise every production station but are deliberately
non-authorizing development/conformance evidence. Promotion remains blocked
until a curated suite explicitly opts into qualification; changing that flag
changes the suite fingerprint and therefore the evidence identity.

### Contracts and workspace

`palimpsest/factory/core/contracts.py` is the artifact type system.
`palimpsest/factory/workspace/layout.py` derives every path from it. The live
contract graph is generated from the contract and station registries:

```bash
python -m palimpsest graph
python -m palimpsest graph --write-docs
```

`docs/CONTRACTS.md` is machine truth and is checked by the test suite.

## Repository layout

```text
palimpsest/
  cli.py                    top-level command entrypoint
  catalog/                  source heads, canonical records, revision store
  factory/
    core/                   contracts, recipes, stations, conductor, ledger
    gateway/                provider-neutral model boundary
    stations/               one module per transformation
    workspace/              atomic I/O and the single path contract
    prompts/                content-hashed prompt files
    recipes/                swappable route sheets
    intake.py               IIIF boundary
    site.py                 static-library renderer
library/
  <doc_id>/                 source records and local factory artifacts
  factory.db                local inventory and production log
  catalog.db                source pointers and immutable record revisions
tests/                      deterministic catalog and factory behavior tests
docs/
  FACTORY.md                architecture and invariants
  CONTRACTS.md              generated live graph
  GLYPHS.md                 alignment and glyph-system design
  EVALUATION.md             evaluation and promotion contracts
  OPERATIONS.md             canonical operator and experiment runbook
```

Generated images, model artifacts, books, runs, the ledger, and the static site
are local and ignored by Git. `metadata.json` and `page_list.json` are the
portable source records for adopted workspaces.

## Design rules

1. One artifact kind, one contract, one path.
2. One transformation, one station, one output.
3. Prompts are files and are content-hashed.
4. Recipes configure behavior; the conductor does not know corpora.
5. Paid work never reruns implicitly after configuration drift.
6. The diplomatic layer is immutable; reconstruction and emendation sit beside it.
7. The ledger is append-only production history, not the archive itself.
8. Presentation consumes the book model and never reaches back into the line.

The pre-cutover repository is preserved remotely on
`archive/pre-factory-cutover-2026-07-20`.
