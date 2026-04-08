# Prompts

Templates for the VLM/LLM calls made by the Palimpsest pipeline. Each `.txt`
file is loaded by name from Python code and formatted at call time.

## Live prompts

These are referenced by live code in `palimpsest/`:

- `opportunity_triage.txt` — cheap metadata + thumbnail triage over discovery
  candidates; loaded by `discovery/triage.py`.
- `transcribe_raw.txt` — raw page-image transcription prompt; default for
  `transcribe.py` (`DEFAULT_PROMPT_NAME = "transcribe_raw"`).
- `survey_brief.txt` — builds a per-document translation brief from chunks of
  raw transcription; loaded by `survey.py`.
- `translate_with_brief.txt` — page-level translation guided by the survey
  brief; loaded by `enrich.py` (`PROMPT_NAME = "translate_with_brief"`).

## Retired / pending retirement

These files still live on disk but have no live code caller. They are remnants
of the retired packet / witness / reader pipeline and will be removed with the
rest of the publishing stack.

- `enrich_translate.txt`
- `packet_translation.txt`
- `page_handoff_focused.txt`
- `window_synthesis_focused.txt`
