# Transcription CLI

This CLI runs the two-pass transcription pipeline and produces:
- per-page JSON outputs (`*_pass1.json`, `*_final.json`)
- run logs (`_runs/`)
- traces (`_traces/`)
- book-level outputs (`book/`)

## Examples

Single page (defaults to transcription_json prompt set if none provided):
```
python -m palimpsest transcribe run \
  --image library/vatican_pal_lat_1267/images/f001r.jpg \
  --out-dir library/vatican_pal_lat_1267/exports/transcriptions_full \
  --prompt-set transcription_json
```

Batch:
```
python -m palimpsest transcribe run \
  --image-dir library/vatican_pal_lat_1267/images \
  --out-dir library/vatican_pal_lat_1267/exports/transcriptions_full \
  --prompt-set transcription_json \
  --pattern "*.jpg" \
  --workers 10 \
  --skip-existing
```

## Status
After any run, inspect:
- `exports/transcriptions_full/_runs/status.json`

## Advanced controls

- `--pass-mode {both,pass1,pass2}`: run only pass1 or only pass2
- `--max-attempts N`: retry limit per pass
- `--no-trace`: skip trace capture (faster, fewer artifacts)
- `--auto-skip-non-text`: auto-skip pass2 when pass1 indicates low-text/blank pages
- `--shard-count N --shard-index K`: split work across shards

## Page typing
The JSON prompts now require `page_type` (e.g., `text_page`, `cover`, `blank`, `ownership`, `binding`, `illustration_only`).
If `--auto-skip-non-text` is set, pass2 will be skipped for non-text page types.

## Model selection

The default model is read from `.env`:
```
PALIMPSEST_MODEL_VISION=gemini-3.1-flash-lite-preview
```
Override with `--model` if needed.

## Orchestrator

For multi-process sharding or two-phase runs, use:
```
python -m palimpsest transcribe shards \
  --image-dir ... \
  --out-dir ... \
  --prompt-set transcription_json \
  --shards 4 \
  --workers-per-shard 10 \
  --two-phase \
  --skip-existing
```
