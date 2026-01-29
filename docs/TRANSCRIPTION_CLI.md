# Transcription CLI

This CLI runs the two-pass transcription pipeline and produces:
- per-page JSON outputs (`*_pass1.json`, `*_final.json`)
- run logs (`_runs/`)
- traces (`_traces/`)
- book-level outputs (`book/`)

## Examples

Single page:
```
python scripts/transcribe_manuscript.py \
  --image projects/vatican_alchemy/images/pal_lat_1267_max/f001r.jpg \
  --out-dir projects/vatican_alchemy/exports/transcriptions_full \
  --prompt-set transcription_json
```

Batch:
```
python scripts/transcribe_manuscript.py \
  --image-dir projects/vatican_alchemy/images/pal_lat_1267_max \
  --out-dir projects/vatican_alchemy/exports/transcriptions_full \
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

## Orchestrator

For multi-process sharding or two-phase runs, use:
```
python scripts/run_transcription.py \
  --image-dir ... \
  --out-dir ... \
  --prompt-set transcription_json \
  --shards 4 \
  --workers-per-shard 10 \
  --two-phase \
  --skip-existing
```
