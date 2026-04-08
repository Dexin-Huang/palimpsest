# Transcription Architecture v2

## Pipeline

```
┌──────────────────────────────────────────────────────────────────────┐
│  [0] PAGE ROUTER                                                     │
│      Cheap classifier: script family, damage level, layout           │
│      complexity, handwritten vs printed                              │
│      → routes to appropriate recognizer config                       │
├──────────────────────────────────────────────────────────────────────┤
│  [1] GEOMETRY                                                        │
│      PP-DocLayoutV3: pixel-accurate segments + reading order         │
│      + few-shot manuscript line segmenter (per manuscript family)    │
│      → ordered line/region polygons                                  │
├──────────────────────────────────────────────────────────────────────┤
│  [2] RECOGNITION (multi-view)                                        │
│      Fine-tuned Qwen3-VL-8B on CHURRO-DS + CATMuS Medieval          │
│      Run each region through multiple views:                         │
│        • original crop                                               │
│        • dewarped                                                    │
│        • contrast-enhanced                                           │
│        • (optional) inverted / thresholded                           │
│      → N candidate transcriptions per region                         │
├──────────────────────────────────────────────────────────────────────┤
│  [3] CONSENSUS / ABSTAIN                                             │
│      Align multi-view outputs character-by-character                 │
│      • Agreement across views → accept                               │
│      • Disagreement → flag with uncertainty marker [?]               │
│      • No confident reading → abstain [...]                          │
│      → single transcription with confidence annotations              │
├──────────────────────────────────────────────────────────────────────┤
│  [4] DIPLOMATIC TRANSCRIPTION                                        │
│      Strict output preserving:                                       │
│        • original spelling and abbreviations                         │
│        • line breaks and layout                                      │
│        • uncertainty markers and provenance                          │
│        • region bounding boxes                                       │
│      → witness.md in page packet                                     │
├──────────────────────────────────────────────────────────────────────┤
│  [5] NORMALIZATION / TRANSLATION (separate, liberal)                 │
│      LLM-powered (Gemini or similar):                                │
│        • abbreviation expansion                                      │
│        • spelling normalization                                      │
│        • translation to modern language                               │
│      Works from verified text, not from images                       │
│      → translation.md in page packet                                 │
└──────────────────────────────────────────────────────────────────────┘
```

## Design Rules

### Transcription is strict
- Multi-view consensus before accepting any reading
- Abstain when uncertain — `[...]` is better than a wrong guess
- No LLM rewriting of transcription output
- Diplomatic fidelity: preserve what's on the page

### Translation is liberal
- Works from verified diplomatic text, not pixels
- LLMs can interpret, expand, normalize freely
- Hallucination here = slightly off translation, not fabricated text

### Diplomatic and normalized are architecturally separate
- `witness.md` = diplomatic transcription (strict)
- `translation.md` = normalized/translated (liberal)
- Never let the translation layer modify the witness

### Active learning
- Uncertain spans flagged by consensus gate → human review queue
- Scholar corrections feed back into recognition model
- Per-manuscript-family fine-tuning of line segmenter (few-shot)

## Models

| Stage | Model | Where it runs |
|-------|-------|---------------|
| Page router | Lightweight classifier (TBD) | Local |
| Geometry | PP-DocLayoutV3 + few-shot line segmenter | Local |
| Recognition | Fine-tuned Qwen3-VL-8B | Local (or RunPod) |
| Consensus | Deterministic alignment | Local |
| Translation | Gemini 3.1 Pro / Flash-Lite | API |

## Training

See `training/` directory for fine-tuning pipeline:
- `training/configs/qwen3vl_8b_qlora.yaml` — production training config
- `training/download_data.py` — fetch CHURRO-DS + CATMuS
- `training/evaluate.py` — CER/NLS evaluation

Trained model hosted on HuggingFace (private): `$HF_MODEL_REPO`
