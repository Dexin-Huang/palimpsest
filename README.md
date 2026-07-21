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
    │  acquire → deframe → dewatermark → flatten → segment → read
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

Copy `.env.example` to `.env` and set `GEMINI_API_KEY`. The two recipe model
defaults can be overridden there:

```env
PALIMPSEST_MODEL_VISION=gemini-flash-latest
PALIMPSEST_MODEL_READING=gemini-flash-lite-latest
```

The `reference` and `emend` stations use an agent executor selected in the
recipe. Their current recipes require either the `codex` or `omp` CLI on
`PATH`; the default is `codex`.

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

The conductor resumes from `library/factory.db`. Fresh cells are skipped;
input drift reruns stale cells; configuration drift is reported as outdated
without silently repeating paid work. Refresh a changed station explicitly:

```bash
python -m palimpsest run \
  --doc-id vatican_pal_lat_1267 \
  --refresh read
```

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

## Commands

| Command | Purpose |
|---|---|
| `init-db` | Initialize the SQLite inventory and production ledger |
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
tests/                      deterministic factory contract and behavior tests
docs/
  FACTORY.md                architecture and invariants
  CONTRACTS.md              generated live graph
  GLYPHS.md                 alignment and glyph-system design
  EVALUATION.md             evaluation, candidate, promotion, and rollback blueprint
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
