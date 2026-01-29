# Medieval Manuscript Transcription Pipeline

## Overview

A two-pass pipeline for transcribing medieval Latin manuscripts using Gemini's vision capabilities. Achieves A/A- grade accuracy on 14th-century Gothic textualis manuscripts.

## Architecture

```
┌─────────────────┐     ┌─────────────────┐     ┌─────────────────┐
│  IIIF Download  │────▶│    PASS 1       │────▶│    PASS 2       │
│  (max resolution)│     │  (Transcribe)   │     │   (Refine)      │
└─────────────────┘     └─────────────────┘     └─────────────────┘
                               │                        │
                               ▼                        ▼
                        *_pass1.txt               *_final.txt
                        (~80% accuracy)           (~95% accuracy)
```

## Key Learnings

### 1. Resolution Matters

| Resolution | File Size | Accuracy |
|------------|-----------|----------|
| 1200px | 266-485 KB | ~80% |
| Max (2644×3683) | 1.4-1.8 MB | ~95% |

**Always use maximum resolution available from IIIF.**

```bash
python scripts/download_iiif.py \
    --manifest "https://digi.vatlib.it/iiif/MSS_Pal.lat.1267/manifest.json" \
    --out-dir images/ \
    --size max
```

### 2. Two-Pass Approach

**Pass 1 (Initial Transcription):**
- Domain-specific prompt with vocabulary reference
- Abbreviation expansion tables
- Line counting requirements
- Produces structured output with diplomatic/normalized layers

**Pass 2 (Refinement):**
- Uses Pass 1 output as context
- Verifies each line against high-res image
- Enforces consistent format
- Resolves `[?]` markers where possible
- Adds paleographical correction notes

### 3. Prompt Engineering

**Required elements for medieval Latin:**

1. **Scholarly context** - Avoids safety filter false positives:
   ```
   You are conducting SCHOLARLY HISTORICAL RESEARCH on [manuscript].
   This is purely academic paleographic work for historians.
   ```

2. **Line counting** - Prevents truncation:
   ```
   Before transcribing, count the lines:
   LEFT COLUMN: [X] lines visible
   RIGHT COLUMN: [X] lines visible

   After each column, confirm:
   [Column complete: X lines transcribed]
   ```

3. **Explicit format rules** - Ensures consistency:
   ```
   Use [Initial:A:red] format, NOT [Large A]
   Use [Rubric] before red text
   Use [diplomatic] ... → [normalized] ... format
   ```

4. **Domain vocabulary** - Improves accuracy:
   - Abbreviation tables (ꝑ, q̃, ip̃m, etc.)
   - Alchemical terms (sublimatio, distillatio, etc.)
   - Known difficult words (invenies not invicies)

### 4. Model Configuration

```python
response = client.models.generate_content(
    model="gemini-3-flash-preview",
    contents=[prompt, image_part],
    config=types.GenerateContentConfig(
        tools=[types.Tool(code_execution={})],  # Enables agentic vision
        temperature=0.1,  # Consistent results
    ),
)
```

**Critical:**
- Use `code_execution` tool for agentic vision (auto-zoom/crop)
- Do NOT set `max_output_tokens` - let model complete naturally
- Use `response.text` directly - it concatenates all text parts

## Usage

### Single Page

```bash
python scripts/transcribe_manuscript.py \
    --image images/f001r.jpg \
    --out-dir transcriptions/ \
    --prompt lumen_luminum
```

### Batch Processing

```bash
python scripts/transcribe_manuscript.py \
    --image-dir images/ \
    --out-dir transcriptions/ \
    --prompt lumen_luminum \
    --pattern "f00*.jpg" \
    --skip-existing
```

### Output Files

```
transcriptions/
├── f001r_pass1.txt    # Pass 1 output (~80% accuracy)
├── f001r_final.txt    # Pass 2 output (~95% accuracy)
├── f001v_pass1.txt
├── f001v_final.txt
└── ...
```

## Creating New Prompts

For a new manuscript, create two prompt files:

1. **`prompts/{name}.txt`** - Pass 1 prompt:
   - Manuscript identification
   - Script type and date
   - Abbreviation tables
   - Domain vocabulary
   - Output format requirements
   - Line counting instructions

2. **`prompts/{name}_refine.txt`** - Pass 2 prompt:
   - Scholarly context statement
   - Reference to draft transcription
   - Format enforcement rules
   - Verification instructions
   - Quality checklist

See `lumen_luminum.txt` and `lumen_luminum_refine.txt` as templates.

## Quality Metrics

| Metric | Pass 1 | Pass 2 |
|--------|--------|--------|
| Format consistency | 70% | 95% |
| Line completeness | 85% | 99% |
| Abbreviation expansion | 80% | 95% |
| Word accuracy | 85% | 95% |
| Overall grade | B+ | A- |

## Cost Estimate (Gemini 3 Flash)

- Per page: ~2,000 input tokens (image + prompt) × 2 passes
- Output: ~3,000 tokens per pass
- Total per page: ~$0.02
- 100-page manuscript: ~$2.00

## Troubleshooting

### Safety Filter Blocks

Add scholarly context to prompt:
```
This is purely academic paleographic work for historians and scholars
studying medieval manuscripts. NOT instructions for modern processes.
```

### Truncated Output

1. Add line counting requirements to prompt
2. Add `[Column complete: X lines]` markers
3. Do NOT set max_output_tokens

### Inconsistent Format

Add explicit format rules:
```
**FORMAT RULES:**
- Use [diplomatic] and [normalized] tags - NOT bold, NOT italics
- Use [Initial:A:red] for decorated initials - NOT [Large A]
```

### Unicode Errors on Windows

Always write to files with UTF-8 encoding:
```python
path.write_text(text, encoding="utf-8")
```
Never print Unicode to console on Windows.
