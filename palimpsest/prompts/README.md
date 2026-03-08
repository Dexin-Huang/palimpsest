# Prompts

This folder contains prompt templates used by the transcription and vision pipelines.

## Two-pass transcription sets

Prompt sets live under `prompts/sets/<name>/` and must include:
- `pass1.txt` (initial transcription)
- `pass2.txt` (refinement, must include `{draft_transcription}` placeholder)

Included sets:
- `sets/lumen_luminum/` (Latin alchemy, Pal.lat.1267)
- `sets/generic/` (time-period agnostic)
- `sets/transcription_json/` (JSON-only, time-period agnostic)

## Legacy prompt names

Some scripts still support the legacy naming convention:
- `<name>.txt` and `<name>_refine.txt`

Example: `lumen_luminum.txt` + `lumen_luminum_refine.txt`

## Other prompts

Other task-specific prompts remain in the root `prompts/` directory.

Reading / interpretation prompts:
- `page_witness_focused.txt` - default witness-heavy page memo prompt
- `section_synthesis_focused.txt` - multi-page translation and interpretation prompt

Current design rule:
- prefer deterministic page preparation over prompt-heavy region/workspace orchestration
