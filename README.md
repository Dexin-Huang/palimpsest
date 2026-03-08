# Palimpsest

Palimpsest is a library-first factory for turning digitized manuscripts into
clean, searchable outputs with full provenance.

Core idea: a stable per-page JSON is the canonical truth. Everything else is
derived (books, HTML viewers, overlays).

North star: recover neglected knowledge traditions from archival corpora.
Transcription is the evidence layer, not the end goal.

Current product focus: evidence-bound diplomatic restoration first. Readable
book output comes next. Broader discovery and knowledge extraction are
downstream of restoration quality.

## Modules

1) Discovery / Opportunities
   - Crawl metadata, score "interestingness", and maintain a master list.

2) Processing / Transcription
   - Download full-res images, run a two-pass transcription, emit `canonical.page`, and assemble diplomatic restoration outputs.

3) Recreation (future)
   - Generate visual reconstructions, overlays, and later readable editions.

## Quickstart (Golden Path)

1) Crawl a range and append to master list:
```
python scripts/palimpsest.py discovery run --collection Pal.lat --range 1200-1400 --limit 200 --output discovery/registry/pal_lat_1200-1400_inventory.jsonl
```

2) Filter interesting candidates (metadata-only pass):
```
python scripts/palimpsest.py discovery filter \
  --input discovery/registry/pal_lat_1200-1400_inventory.jsonl \
  --output discovery/registry/pal_lat_1200-1400_interesting.jsonl
```

3) Create a library record from a manifest:
```
python scripts/palimpsest.py library intake \
  --doc-id vatican_pal_lat_1267 \
  --manifest https://digi.vatlib.it/iiif/MSS_Pal.lat.1267/manifest.json
```

4) Run the full pipeline (download -> transcribe -> canonicalize -> restore):
```
python scripts/palimpsest.py library run --doc-id vatican_pal_lat_1267
```

`library run` now materializes three primary output lanes:
- `exports/transcriptions_full/`
- `exports/canonical_pages/`
- `exports/restoration/`

5) Assemble diplomatic restoration outputs from canonical page JSON:
```
python -m palimpsest book restore --pages-dir path/to/canonical_pages
```

Witness and synthesis reading lane:
```
python -m palimpsest page prepare --image library/<doc_id>/images/<page>.jpg
python -m palimpsest page packet --image library/<doc_id>/images/<page>.jpg
python -m palimpsest page read --image library/<doc_id>/images/<page>.jpg
python -m palimpsest page handoff --packet library/<doc_id>/experiments/<page>_packet/packet.json --next-page-id <next_page_id>
python -m palimpsest page window \
  --packet library/<doc_id>/experiments/<page1>_packet/packet.json \
  --packet library/<doc_id>/experiments/<page2>_packet/packet.json
python -m palimpsest page synthesize \
  --input library/<doc_id>/experiments/<page1>_reading/<page1>_reading.md \
  --input library/<doc_id>/experiments/<page2>_reading/<page2>_reading.md \
  --input library/<doc_id>/experiments/<page3>_reading/<page3>_reading.md
```

`page read` now uses deterministic content preparation by default:
- trim the obvious footer / dead lower area
- crop to the manuscript-bearing region
- run the witness prompt on the prepared image

Use `--raw` only when you explicitly want to read the unprepared source image.

`page packet` creates the scholar-facing working bundle for one page unit:
- prepared image
- witness stub
- notes / translation / interpretation stubs
- minimal facing-page LaTeX template

Dedicated scholar agent lane:
```
python -m palimpsest scholar packet \
  --packet library/<doc_id>/experiments/<page>_packet/packet.json \
  --task fill_witness \
  --witness library/<doc_id>/experiments/<page>_reading/<page>_reading.md
python -m palimpsest scholar packet \
  --packet library/<doc_id>/experiments/<page>_packet/packet.json \
  --task render_edition
python -m palimpsest page render \
  --packet library/<doc_id>/experiments/<page>_packet/packet.json
```

This is separate from the general Claude grunt-worker commands. It is scoped to
one `page.packet` workspace and is intended to act like a scholar moving
front-to-back through the manuscript.

`page render` is the deterministic build step for packet editions. It uses the
local `tectonic` binary when available and updates the packet with the compiled
PDF artifact.

Curated source-adapter lane:
```
python -m palimpsest discovery sources list
python -m palimpsest discovery sources scrape --source idp --collection chinese_magic --limit 5
python -m palimpsest discovery sources scrape --source gallica --collection chinese_divination --limit 5
python -m palimpsest discovery sources scrape --source idp --collection stein_dunhuang_chinese --max-pages 2 --output discovery/idp_stein_dunhuang.jsonl
python -m palimpsest discovery sources ingest --source idp --collection chinese_daoism --limit 5 --triage
python -m palimpsest discovery scout --repository IDP --min-score 8 --limit 12 --with-web-search
```

`discovery sources list` is the source radar:
- `automation_fit` tells you how well the source can run unattended
- `north_star_fit` tells you how well it matches vanished-world discovery
- `access` tells you the expected delivery mode

Manual rebuild from legacy transcription outputs:
```
python -m palimpsest book canonicalize --transcriptions-dir path/to/transcriptions_full --images-dir path/to/images
python -m palimpsest book restore --pages-dir path/to/canonical_pages
```

Single entrypoint:
```
python -m palimpsest <command> ...
```

All files in `scripts/` are thin wrappers around the unified CLI.

Claude SDK helper:
```
python -m palimpsest agent "Inspect the repo and patch the bug"
python -m palimpsest agent-edit "Apply the requested patch"
python -m palimpsest agent-inspect "Find the bug and cite the files involved"
python -m palimpsest agent-summarize "Summarize the manuscript notes"
python -m palimpsest agent-inspect --with-web-search "Find the official viewer URL and reply with one link"
python -m palimpsest agent-batch --input jobs.jsonl --concurrency 3 --json
```

Operator note for future sessions:
- `docs/AGENT_WORKERS.md`

Batch file example (`jobs.json` or `jobs.jsonl`):
```json
[
  {
    "id": "inspect-1",
    "profile": "inspect",
    "workspace": "D:/Projects/palimpsest",
    "prompt": "Find the file containing DEFAULT_MODEL_AGENT."
  },
  {
    "id": "edit-1",
    "profile": "edit",
    "workspace": "D:/Projects/palimpsest",
    "prompt": "Update the requested file and reply with one sentence."
  },
  {
    "id": "web-1",
    "profile": "inspect",
    "workspace": "D:/Projects/palimpsest",
    "with_web_search": true,
    "prompt": "Find the current official viewer URL and reply with one link."
  }
]
```

User-local CLI install:
```
python -m pip install --user --editable .
```

Windows helper:
```
powershell -ExecutionPolicy Bypass -File scripts/install-cli.ps1
```

If `%APPDATA%\npm` exists, the helper also drops a `palimpsest` shim there so it behaves like `playwright-cli` on this machine.

Defaults: transcription uses the `transcription_json` prompt set unless overridden.
For a slimmer witness-only experiment, try `--prompt-set transcription_minimal_json`.
For page-level witness memos, see `palimpsest/prompts/page_witness_focused.txt`.
For cross-page translation and interpretation, see `palimpsest/prompts/section_synthesis_focused.txt`
and `docs/READING_PROMPTS.md`.
Model lane policy and fine-tuning thresholds live in `docs/MODEL_STRATEGY.md`.

## Layout (Library First)

```
library/
  <doc_id>/
    metadata.json
    page_list.json
    images/
    exports/
      transcriptions_full/
      canonical_pages/
      restoration/
      book/        # legacy/manual assembly lane
```

## Configuration

Create a local `.env` (see `.env.example`):

- `GEMINI_API_KEY`
- `PALIMPSEST_MODEL_TRIAGE` (default: gemini-3.1-flash-lite-preview)
- `PALIMPSEST_MODEL_VISION` (default: gemini-3-flash-preview)
- `PALIMPSEST_MODEL_READING` (default: gemini-3-flash-preview, reserved for post-transcription reading/extraction)
- `PALIMPSEST_MODEL_RECON` (optional)
- `PALIMPSEST_MODEL_AGENT` (default: claude-sonnet-4-5)
- `PALIMPSEST_TECTONIC_BIN` (optional explicit path to `tectonic`)
- `PALIMPSEST_EDITION_FONT_LATIN` (optional font file path or family name)
- `PALIMPSEST_EDITION_FONT_CJK` (optional font file path or family name)

Use `*-image-preview` models only for reconstruction or other generation/editing lanes. The main page-evidence transcription path should stay on a text-output multimodal model such as `gemini-3-flash-preview`.

Packet edition rendering uses this font policy:
- environment override first
- bundled fonts under [fonts/README.md](D:/Projects/palimpsest/fonts/README.md)
- system fallback last

## Docs

- `docs/VISION.md` - system vision and data model
- `docs/ARCHITECTURE.md` - detailed intake -> processing -> output architecture
- `docs/PRODUCT_FOCUS.md` - current product wedge: diplomatic restoration first
- `docs/PAGE_EVIDENCE_SCHEMA.md` - canonical per-page evidence schema, including restoration/typesetting support
- `docs/DIPLOMATIC_RESTORATION_CONTRACT.md` - first serious output contract: `diplomatic.page` and `diplomatic.book`
- `docs/READING_PROMPTS.md` - focused page-level reading prompts separate from witness extraction
- `docs/PAGE_PACKET.md` - scholar-facing page packet for notes, translation, and edition work
- `docs/CONTINUITY_STATE.md` - page handoffs and sliding-window state for front-to-back reading
- `docs/SOURCE_ADAPTERS.md` - minimal source-adapter layer for the virtual Library of Alexandria
- `docs/DISCOVERY_SYSTEM.md` - autonomous discovery loop, source-fitness rubric, and DB-first flow
- `docs/MODEL_STRATEGY.md` - current lane-by-lane model choices and fine-tuning trigger rules
- `docs/knowledge_recovery_vision.md` - higher-level research vision for recovering neglected knowledge traditions
- `docs/FACTORY.md` - module summary
- `docs/AGENT_WORKERS.md` - local Claude worker commands and handoff guidance
- `docs/TRANSCRIPTION_CLI.md` - transcription CLI usage
- `docs/PHILOSOPHY.md` - repository philosophy and guardrails

## References

- `references/README.md` - purpose and conventions for upstream references
- `references/github_repos.md` - curated GitHub repo shortlist
- `references/github_repos.json` - machine-readable manifest of the same repo set
