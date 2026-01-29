# Transcription Pipeline

This package provides the production-grade transcription pipeline used by `scripts/transcribe_manuscript.py`.

## Modules
- `config.py`: `PromptConfig` and `RunConfig` (the pipeline's config surface).
- `prompts.py`: prompt loading helpers.
- `io.py`: atomic file writes and JSON validation.
- `runlog.py`: run metadata, events/errors logs, status snapshots.
- `trace.py`: agentic response tracing (code blocks, images, usage, text).
- `runner.py`: core orchestration (`run_single`, `run_batch`).

## Outputs
All outputs live under the `out_dir` you pass to the runner. In JSON mode it writes:
- `*_pass1.json` and `*_final.json`
- `_runs/` (run metadata + events/errors + status)
- `_traces/` (full agentic response traces)
- `../book/` (assembled book outputs)

## Public API
```
from palimpsest.transcription import PromptConfig, RunConfig, run_batch, run_single
```

## RunConfig fields
- `pass_mode`: `both` (default), `pass1`, or `pass2`
- `max_attempts`: retry limit per pass
- `trace`: enable/disable trace capture
- `auto_skip_non_text`: skip pass2 when pass1 suggests low-text/blank pages
- `shard_count` / `shard_index`: shard control for parallel runs

## Manual flags
To skip pass2 for specific pages, create `page_flags.json` in the output directory:
```
{
  "skip_pass2": {
    "f001r": "cover page",
    "page_0000": "binding"
  }
}
```
See `palimpsest/transcription/page_flags.example.json`.

## Page typing
Prompts include `page_type` (e.g., `text_page`, `cover`, `blank`, `ownership`, `binding`, `illustration_only`).
If `auto_skip_non_text` is enabled, pass2 will be skipped for non-text page types.
