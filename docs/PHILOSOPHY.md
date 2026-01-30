# Repository Philosophy

Palimpsest is a repeatable factory for turning digitized manuscripts into a
clean, searchable library. The repo is optimized for clarity, auditability, and
scale; it favors a single golden path over optional, ad-hoc workflows.

## Core Principles
- Library-first: every document lives under `library/<doc_id>/` with stable
  metadata, page lists, images, and exports.
- Canonical JSON: per-page JSON outputs are the source of truth; everything
  else (books, HTML, overlays) is derived.
- Golden path by default: defaults should match the preferred workflow; avoid
  fallbacks unless necessary.
- Modular, small scripts: avoid monoliths; keep functions single-purpose and
  inspectable.
- Config once: model and runtime defaults live in `palimpsest/config.py` and
  `.env`; do not scatter defaults across scripts.
- Prompts are external files: prompt text belongs under `palimpsest/prompts/`.
- Auditability: write intermediate results immediately; keep logs and status
  per document.

## Repo Layout Rules
- `palimpsest/` contains the core package and modules.
- `scripts/` are thin wrappers for the unified CLI (no logic).
- `library/` is the canonical output root.
- `discovery/` holds registries, manifest cache, and crawl artifacts.
- `docs/` contains the system documentation and vision.

## Change Discipline
- Prefer adding new functionality inside `palimpsest/commands/` and wiring it
  to the CLI, not as new one-off scripts.
- When adding a new module, keep its public interface minimal and document it.
- Ensure outputs are deterministic, reproducible, and resumable.
- Keep file names and IDs stable; avoid silent renames.

## Operational Defaults
- The CLI should run end-to-end with zero optional flags.
- The system should be safe to resume at any time.
- Failures should be explicit and leave the workspace consistent.
