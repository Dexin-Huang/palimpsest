# The Factory

System design for the modular re-architecture of Palimpsest. This supersedes
the ad-hoc stage wiring that grew during the publishing era and defines the
target shape everything migrates toward.

Status: **accepted** (2026-07-08). Grounded in a full coupling audit of the
current package; the specific defects each element fixes are cited inline.

Shape source of truth: [`docs/BLUEPRINT.html`](BLUEPRINT.html) — every
artifact schema, station I/O, and the recipe format, each marked
confirmed/proposed/corrected. Shapes are contracts only once confirmed there.

> **Correction (2026-07-08).** The addendum's "Phase A honeycomb sidecar
> bypass" premise is invalid: Ariadne has no sidecar ingestion path, its v0.3
> spec explicitly forbids sidecar metadata files, and the manifest-tree format
> the three-file bundle targeted is the superseded v0.2 design. The handoff is
> **one clean markdown file** consumed by `ariadne v3 ingest manifestation`;
> the manifest/anchors sidecars remain Palimpsest-internal provenance. Details
> and the corrected `emit` contract: BLUEPRINT.html §7. Mentions of the
> "three-file bundle consumed by Ariadne's honeycomb" elsewhere in this
> document should be read through that correction.

Build strategy: **greenfield in a new subpackage** (`palimpsest/factory/`).
The old pipeline modules stay untouched and runnable while the factory is
built beside them. Once the factory reproduces the golden path (verified by
diffing outputs on a reference document), the pre-factory state is archived
on a remote branch (`archive/pre-factory`) and the old modules are deleted
from the working tree. No in-place porting, no half-migrated limbo.

---

## 1. The picture

Palimpsest is a factory. Scout heads roam the archives and feed interesting
items onto the line. The line runs two nested loops: a **page line** (the small
loop) that turns one page image into an assembled bilingual page record, and a
**manuscript line** (the big loop) that turns a tray of assembled pages into a
reconstructed manuscript — original text plus English translation — packaged
as the Ariadne handoff bundle.

```
   SCOUT HEADS                    THE LINE
  ┌───────────┐
  │ vatican   │──┐
  │ idp       │──┤   ┌────────┐   ┌─────────────────────────────────────┐
  │ gallica   │──┼──▶│ triage │──▶│ promote ──▶ WORK ORDER enters line  │
  │ (more…)   │──┘   └────────┘   └─────────────────────────────────────┘
  └───────────┘                                     │
                                                    ▼
  MANUSCRIPT LINE (big loop) ────────────────────────────────────────────
  │                                                                     │
  │  intake ─▶ ┌─ PAGE LINE (small loop, ×N pages, parallel) ─────────┐ │
  │            │                                                      │ │
  │            │  acquire ─▶ prepare ─▶ read ─▶ translate ─▶ assemble │ │
  │            │  (download)  (clean)   (VLM)   (w/ brief)   (page)   │ │
  │            └──────────────────────────────────────────────────────┘ │
  │                 ▲                                                   │
  │   survey ───────┘ (builds the "jig": glossary/brief the page       │
  │   (ms-level)       line's translate station clamps into)           │
  │                                                                    │
  │  reconstruct ─▶ emit                                               │
  │  (boundary      (Ariadne bundle: <doc_id>.md + .manifest.json      │
  │   repair,        + .anchors.json)                                  │
  │   collation)                                                       │
  └────────────────────────────────────────────────────────────────────┘
```

Every box is a **station**. Stations are interchangeable parts: same
interface, different internals. What runs at each slot — which prompt, which
model, which language, which cleaning profile — is decided by a **recipe**,
not by code.

---

## 2. Core concepts

### 2.1 Work order

The unit of factory work. One work order = one item (a manuscript, a codex, a
bound volume of prefaces). It carries:

- `doc_id` — stable identity, same as today's `library/<doc_id>/`
- `recipe` — which route sheet this item follows
- provenance of how it entered the line (which scout head, triage score,
  prospect record) — nothing is re-typed by hand

A work order's pages are its sub-units. Page-line stations operate per page;
manuscript-line stations operate on the whole order.

### 2.2 Station

A station is one processing step with one uniform contract:

```python
class Station(Protocol):
    name: str                      # "read", "translate", "prepare", …
    consumes: tuple[Kind, ...]     # artifact kinds it requires
    produces: tuple[Kind, ...]     # artifact kinds it emits

    def run(self, order: WorkOrder, ws: Workspace, cfg: StationConfig) -> StationResult: ...
```

Rules:

- **Stateless.** A station reads artifacts from the workspace, writes
  artifacts to the workspace, and reports what it did. All memory lives in
  the ledger (§2.5), never inside the station.
- **Declared I/O.** `consumes`/`produces` are artifact *kinds* (e.g.
  `page_image`, `page_image_clean`, `page_transcription`, `translation_brief`,
  `page_assembled`, `bundle`). The conductor uses these to order stations and
  to validate recipes at load time — a recipe that wires `translate` before
  anything produces `page_transcription` fails before a single API call.
- **Provenance-stamped.** Every artifact a station writes carries a
  provenance record: station name + version, model id, prompt name + content
  hash, generation params, token usage, cost, timestamp. This is
  non-negotiable — it is what makes the library trustworthy and what Ariadne's
  source-grounding requires.
- **Registered by name.** A station registry maps `name → implementation`, so
  recipes reference stations by string and new stations plug in without
  touching the conductor.

Stations replace the current stage functions. Notably there is **one** `read`
station slot, not separate OCR + transcribe stations: we are VLM-native and
have no legacy HTR pass. A corpus that genuinely needs two reading passes
(e.g. raw read then diplomatic normalization) expresses that as two `read`
stations in its recipe with different prompts — the slot is generic, the
recipe decides.

### 2.3 Recipe (route sheet)

A recipe is data, not code: a YAML file naming the ordered stations for an
item class and binding each slot's swappable parts.

```yaml
# recipes/latin_manuscript.yaml
name: latin_manuscript
language: la
line:
  page:
    - station: acquire
    - station: prepare
      profile: parchment_default
    - station: read
      model: ${PALIMPSEST_MODEL_VISION}
      prompt: read/la/diplomatic_json
      params: { temperature: 0.1, media_resolution: high }
    - station: translate
      model: ${PALIMPSEST_MODEL_READING}
      prompt: translate/la/with_brief
      requires_jig: translation_brief
    - station: assemble_page
  manuscript:
    - station: survey            # produces the translation_brief jig
      model: ${PALIMPSEST_MODEL_READING}
      prompt: survey/la/brief
    - station: reconstruct
      prompt: reconstruct/boundary_repair
    - station: emit
      target: ariadne_bundle
```

Hot-swapping is now a one-line diff: a Greek papyri corpus is
`recipes/greek_papyri.yaml` with different prompts and maybe a different
`prepare` profile; trying a new model on the read slot is one field. **No
recipe change ever requires a code change** unless a genuinely new station
kind is being built.

This directly fixes the audit's finding that today, swapping the enrich
prompt, changing any temperature, or using a non-default model per stage
requires editing module constants.

### 2.4 Conductor

The orchestrator. `palimpsest run --doc-id X` (recipe comes from the work
order) does:

1. Load recipe, validate station graph against the registry.
2. Read the ledger; compute which stations still owe artifacts, per page and
   per manuscript.
3. Run page-line stations with bounded parallelism across pages (one shared
   worker-pool implementation — replacing the four independent hand-rolled
   concurrency patterns in transcribe/enrich/survey/triage today).
4. Run manuscript-line stations when their inputs are complete (survey can
   run as soon as all reads are done, while late pages are still translating
   only if the recipe says the brief is required — the conductor respects
   `requires_jig`).
5. Record every transition in the ledger. Crash-safe: re-running `run` always
   resumes from the ledger, never re-does paid work, never needs
   `--skip-existing` flags.

The conductor is the *only* component that knows about ordering and
concurrency. Stations never call each other and never import each other's
internals.

### 2.5 The ledger: inventory + production log

One SQLite database, `library/factory.db`, is the factory's memory. It is
both the **inventory** (what the scouts have found, what's on the line) and
the **production log** (what work was done, by which process version, at
which cost). Three tables:

```sql
-- Everything the scout heads find. One row per prospect, promoted or not.
CREATE TABLE prospects (
  prospect_id   TEXT PRIMARY KEY,
  head          TEXT NOT NULL,        -- 'vatican' | 'idp' | 'gallica' | …
  archive_ref   TEXT NOT NULL,        -- shelfmark / ark / external id
  manifest_url  TEXT,
  title         TEXT,
  language      TEXT,
  date_range    TEXT,
  triage_score  INTEGER,
  triage_json   TEXT,                 -- full triage reasoning, provenance
  found_at      TEXT NOT NULL,
  status        TEXT NOT NULL         -- found | triaged | promoted | rejected
);

-- Work orders: prospects that were promoted onto the line.
CREATE TABLE items (
  doc_id       TEXT PRIMARY KEY,
  prospect_id  TEXT REFERENCES prospects(prospect_id),
  recipe       TEXT NOT NULL,
  mode         TEXT NOT NULL,         -- 'source' | 'opportunity'
  promoted_at  TEXT NOT NULL,
  status       TEXT NOT NULL          -- active | complete | parked | failed
);

-- Append-only production log. One row per station execution, page-grained
-- for page-line stations (page_id set), item-grained for manuscript-line
-- stations (page_id NULL). Rows are never updated or deleted.
CREATE TABLE stage_runs (
  run_id             INTEGER PRIMARY KEY,
  doc_id             TEXT NOT NULL REFERENCES items(doc_id),
  page_id            TEXT,            -- NULL for manuscript-line stations
  station            TEXT NOT NULL,   -- 'prepare' | 'read' | 'translate' | …
  status             TEXT NOT NULL,   -- running | done | failed:<reason>
  station_version    TEXT NOT NULL,   -- 'read/v2'
  model              TEXT,
  prompt_name        TEXT,
  prompt_hash        TEXT,            -- content hash of the exact prompt text
  params_hash        TEXT,            -- generation params fingerprint
  config_fingerprint TEXT NOT NULL,   -- hash(station_version+model+prompt+params)
  input_fingerprint  TEXT NOT NULL,   -- hash of upstream artifacts consumed
  output_fingerprint TEXT,            -- hash of what this run produced; downstream
                                      -- staleness is detected against this (§2.6)
  output_path        TEXT,
  tokens_in          INTEGER, tokens_out INTEGER, cost_usd REAL,
  started_at         TEXT NOT NULL, finished_at TEXT,
  error              TEXT
);
```

`prospects ⋈ items ⋈ stage_runs` answers both the inventory questions
("what has the Gallica head found above score 80 that we haven't run yet?")
and the production questions ("which pages of Pal.lat.1267 were read by
`read/v1` on the old flash model, and what did each cost?"). The **current
state** of the line is a view — latest successful run per
(doc, page, station) — not a mutable column, so history is never lost.

Because `stage_runs` is append-only, "page 0042 scanned by process v1, model
X" and its later refresh by v2 are both permanent records. The DB is an
index, not the archive: every artifact on disk also carries its own embedded
provenance stamp, so `factory.db` can be rebuilt from the workspace if it is
ever lost (design rule §6.4 makes this possible).

This replaces: the library `metadata.json` status string that stops updating
after download, the `--skip-existing` output-scanning, the per-batch-run-only
`batch_manifest.json`, and the separate discovery SQLite DB (the `prospects`
table absorbs it). `metadata.json` stays — it holds *what the item is*; the
ledger holds *where it is on the line and how it got there*.

### 2.6 Staleness and stage refresh

The factory is an incremental build system. Freshness is decided by the two
fingerprints every run records:

- **Config drift** — the recipe now binds this station to a different
  model/prompt/params/station-version than the latest run used
  (`config_fingerprint` mismatch). The stage is **outdated**. Outdated work
  is *not* redone automatically — re-running paid API work is always an
  explicit decision.
- **Input drift** — an upstream station was re-run, so this stage's recorded
  `input_fingerprint` no longer matches the current upstream artifacts. The
  stage is **stale**. Stale artifacts no longer derive from what's above
  them, so plain `run` *does* redo them to restore line consistency.

The operator loop this enables:

```
# A better vision model ships. Point the recipe's read slot at it, then:
palimpsest status --doc-id X          # read: outdated (config drift), rest: fresh
palimpsest run --doc-id X --refresh read
                                      # read re-runs page by page under the new
                                      # config; translate/assemble/reconstruct
                                      # flip to stale as each page's read lands
palimpsest run --doc-id X             # rebuilds everything stale downstream
```

Refresh is per-stage and page-granular: refreshing `read` never touches
`acquire`/`prepare` artifacts, and a page whose new read output is
byte-identical to the old one (same output hash) does not propagate staleness
downstream — no cascade without cause.

### 2.7 Scout heads

Discovery becomes a set of independent **heads**, each an adapter over one
archive (Vatican, IDP, Gallica today; more later). Heads share one contract:
emit rows into the `prospects` table (§2.5). Triage scores them in place.
Then the seam that is missing today:

**`promote`** — converts an approved prospect into a work order: inserts an
`items` row referencing the prospect (so scout provenance is a join away,
never re-typed), creates `library/<doc_id>/` with metadata drawn from the
prospect record, and assigns a recipe by corpus rules. From that moment the
item is on the line and `palimpsest run` carries it to the end.

Promotion can be gated (`--auto` above a score threshold, or interactive
review of the shortlist) — the gate is policy, the seam is code.

This is also where dual-mode lives cleanly: **source-mode** prospects promote
into the manuscript line; **opportunity-mode** prospects (labor-killed dreams
in prefaces) promote into a different, cheaper recipe whose line might be just
`acquire → read → extract_dream` — same factory, different route sheet.

### 2.8 Shared services (factory utilities)

The infrastructure every station uses, built exactly once:

- **Model gateway** — one client seam wrapping providers (Gemini `genai`
  today, Claude SDK for agentic scouts, others later). One place for: client
  lifecycle, retries, response parsing, JSON-fence stripping, token
  accounting, cost metering (pricing tables live here), and the Batch API
  lifecycle as an alternate execution mode of the same gateway. Replaces the
  five duplicated call sites and two parallel model stacks.
- **Prompt store** — all prompts are files under
  `palimpsest/prompts/<station>/<language>/<name>.txt`, resolved by one
  loader, content-hashed for provenance. Replaces today's four binding
  mechanisms (including the prompt embedded as a Python string in scout.py).
- **Artifact I/O** — one module for atomic JSON/JSONL read/write (the
  existing `library/io.py` atomic writer, promoted to package-wide use) and
  the workspace path contract (one `layout.py`; today there are three
  `PROJECT_ROOT` definitions and stations that bypass `contracts.py` with
  string literals).
- **Config** — one `config.py`: env-backed model defaults, library root,
  factory DB path. No more per-subcommand hardcoded path defaults.

---

## 3. The two loops, precisely

### Page line (small loop) — per page, parallel across pages

| Station | Consumes | Produces | Swappable parts |
|---|---|---|---|
| `acquire` | page_list entry | `page_image` | IIIF size/quality params |
| `prepare` | `page_image` | `page_image_clean` | cleaning profile |
| `read` | `page_image_clean` | `page_transcription` | model, prompt, params |
| `translate` | `page_transcription`, `translation_brief` (jig) | `page_translation` | model, prompt, target language |
| `assemble_page` | `page_transcription`, `page_translation` | `page_assembled` | layout of the bilingual record |

`page_assembled` is the small loop's finished part: one record holding the
original transcription and its translation, aligned, with anchors and full
provenance. (Today's `enriched.jsonl` records are the proto-form of this.)

### Manuscript line (big loop) — per item

| Station | Consumes | Produces | Notes |
|---|---|---|---|
| `survey` | all `page_transcription` | `translation_brief` | The **jig**: glossary, outline, named entities, style guide that the page line's `translate` clamps into. Runs after reads, before translations. |
| `reconstruct` | all `page_assembled` | `manuscript_original`, `manuscript_translation` | Cross-page boundary repair, collation into continuous original text + continuous English, section structure recovered. |
| `emit` | reconstruction outputs | `bundle` | The Ariadne handoff: `<doc_id>.md` + `<doc_id>.manifest.json` + `<doc_id>.anchors.json`. This is Phase B from the restructuring addendum, landing as a station. |

The big loop is deliberately thin right now — `reconstruct` is the station
with the most unbuilt substance (it absorbs today's enrich-time overlap/
boundary-repair logic and extends it to full collation). More manuscript-level
stations (e.g. `verify`, `illuminate`/image-reconstruction lanes using the
`*-image-preview` models) slot in later without touching anything else.

---

## 4. Package layout (target)

Everything new lives under one subpackage, `palimpsest/factory/`, so the old
world is deletable in a single stroke when parity lands:

```
palimpsest/
  factory/                    # ← the entire new world
    core/
      station.py              # Station protocol, artifact kinds, provenance record
      registry.py             # station name → implementation
      recipe.py               # recipe load + validation
      conductor.py            # orchestration, worker pool, staleness resolution
      ledger.py               # factory.db access: prospects / items / stage_runs
    stations/
      acquire.py  prepare.py  read.py  translate.py  assemble_page.py
      survey.py   reconstruct.py  emit.py
    scouts/
      heads/                  # vatican.py, idp.py, gallica.py (adapter contract)
      triage.py
      promote.py              # prospect → work order (the new seam)
    gateway/
      client.py               # provider seam
      gemini.py  claude.py
      pricing.py  batch.py
    prompts/<station>/<language>/<name>.txt
    recipes/*.yaml
    workspace/
      layout.py               # the one path contract
      io.py                   # atomic writers, JSONL
    config.py
    cli.py                    # wired as `palimpsest factory …` during build,
                              # promoted to the top-level CLI at cutover
  …existing modules…          # untouched until cutover, then deleted
```

CLI surface collapses toward: `palimpsest scout …`, `palimpsest promote …`,
`palimpsest run --doc-id X`, `palimpsest status [--doc-id X]`. Stage-level
control stays as flags (`palimpsest run --only read`, `--refresh read`) but
the golden path is one verb.

---

## 5. Build plan (greenfield)

The factory is built fresh under `palimpsest/factory/`, lifting logic from
the old modules where it's sound but never importing them. The old pipeline
keeps working throughout — it is the reference implementation the factory is
verified against.

1. **Skeleton + utilities.** Scaffold the subpackage; build `gateway/`,
   prompt store, `workspace/`, `config.py`, and the ledger schema
   (`factory.db`). These have no dependency on pipeline semantics and
   everything else stands on them.
2. **Station protocol + page line.** Station/registry/recipe/conductor core,
   then the page-line stations (`acquire`, `prepare`, `read`, `translate`,
   `assemble_page`) — logic lifted from `download.py`, `clean.py`,
   `transcribe.py`, `enrich.py`, re-homed onto the gateway and ledger.
3. **Manuscript line.** `survey` (lifted), then the new substance:
   `reconstruct` and `emit` (addendum Phase B — the Ariadne bundle).
4. **Parity gate.** Run old pipeline and factory on the same reference
   document (Pal.lat.1267); diff transcription/translation outputs. The
   factory must reproduce the golden path before anything is deleted.
5. **Scouts + promote.** Heads re-homed under the adapter contract, triage
   ported, `prospects` migration from the old discovery DB, `promote` built
   (this is where addendum Phase C Gallica and Phase D `Prospect` schema
   land).
6. **Cutover.** Push branch `archive/pre-factory` to the GitHub remote;
   delete the old modules from the working tree; promote the factory CLI to
   the top level; rewrite README/CLAUDE.md command surfaces. (Addendum
   Phase E — vestigial cleanup — happens implicitly here, since the dead
   weight simply doesn't come along: `packets/`, `compat.py`, orphaned
   prompts, re-export shims.)

Ordering rationale: utilities before stations because every station stands on
them; page line before manuscript line because the big loop consumes the
small loop's parts; the parity gate before scouts because parity is what
licenses deletion, and scouts can land while cutover is being prepared.

---

## 6. Design rules (what keeps it from re-convolving)

1. Stations never import other stations. Cross-station data flows only
   through workspace artifacts.
2. The conductor is the only component with ordering/concurrency knowledge.
3. Anything a recipe can express must not be expressible in code too — one
   place, one mechanism (kills config drift).
4. Every artifact write is atomic and provenance-stamped, or it doesn't
   merge. Disk artifacts are the archive; `factory.db` is an index over them
   and must remain rebuildable from the workspace alone.
5. Prompts are files. A prompt in a Python string is a bug.
6. New corpus = new recipe (+ maybe a scout head). If a new corpus requires
   editing the conductor, the design has failed — file it as such.
7. `stage_runs` is append-only. History of what was produced by which
   process version is never overwritten — it is the inventory's audit trail.
