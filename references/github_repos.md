# GitHub Repos

This is the current shortlist of upstream repos that are especially relevant
to Palimpsest.

## Agentic Research Loops

### `karpathy/autoresearch`
- URL: <https://github.com/karpathy/autoresearch>
- Why it matters:
  Minimal autonomous research loop with a fixed objective and iterative code
  improvement. This is the closest reference for a future Palimpsest
  reconstruction-policy loop.

### `karpathy/nanochat`
- URL: <https://github.com/karpathy/nanochat>
- Why it matters:
  Lightweight LLM training core that sits behind `autoresearch`. Useful as a
  reference for small, hackable experiment loops.

## Document OCR / Parsing

### `allenai/olmocr`
- URL: <https://github.com/allenai/olmocr>
- Why it matters:
  Strong reference for large-scale document OCR pipelines, evaluation, and
  operational batch processing.

### `PaddlePaddle/PaddleOCR`
- URL: <https://github.com/PaddlePaddle/PaddleOCR>
- Why it matters:
  Broad document OCR and parsing stack with strong multilingual coverage.

### `datalab-to/marker`
- URL: <https://github.com/datalab-to/marker>
- Why it matters:
  Practical PDF-to-structured-text pipeline with a strong emphasis on usable
  downstream outputs.

### `datalab-to/surya`
- URL: <https://github.com/datalab-to/surya>
- Why it matters:
  Useful reference for detection and layout primitives that may be worth
  borrowing for page scaffolding or region proposals.

## Multimodal Models / Training

### `QwenLM/Qwen3-VL`
- URL: <https://github.com/QwenLM/Qwen3-VL>
- Why it matters:
  Open multimodal model family with fine-tuning relevance for future
  manuscript specialists and adapter-based routing.

## SDKs / Agent Tooling

### `googleapis/python-genai`
- URL: <https://github.com/googleapis/python-genai>
- Why it matters:
  Official Gemini Python SDK used directly in Palimpsest.

### `openai/openai-agents-python`
- URL: <https://github.com/openai/openai-agents-python>
- Why it matters:
  Useful reference for tool-driven agent orchestration patterns and agent
  runtime structure.

## Palimpsest Angle

The most immediately relevant references for the current page-workspace and
agentic reconstruction direction are:

1. `karpathy/autoresearch`
2. `allenai/olmocr`
3. `datalab-to/surya`
4. `openai/openai-agents-python`
