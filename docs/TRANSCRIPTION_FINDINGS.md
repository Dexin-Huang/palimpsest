# Transcription Findings (Jan 2026)

Summary of what improved accuracy for Pal.lat.1267.

## What helped the most

1) Agentic Vision (`code_execution`) for zoom/crop
2) Domain vocabulary and known corrections
3) Line counting requirements in the prompt
4) Explicit output format rules

## What helped less

- Long historical context
- Abbreviation tables without vocabulary context

## Recommended prompt structure

- Manuscript context (date, script, domain)
- Minim disambiguation rules
- Expected vocabulary
- Known bad -> correct pairs
- Output schema (JSON)
- Line counting + self-checks

## Model config (current default)

```
PALIMPSEST_MODEL_VISION=gemini-3-flash-preview
```

Use Agentic Vision with code execution; do not cap max output tokens.

## Files

- `palimpsest/prompts/sets/transcription_json/`
- `python -m palimpsest transcribe run`
- `palimpsest/transcription/runner.py`
