# Gemini 3 Flash: Agentic Vision - Complete API Documentation

> Documentation fetched: 2026-01-29
> Sources:
> - https://ai.google.dev/gemini-api/docs/gemini-3
> - https://ai.google.dev/gemini-api/docs/code-execution
> - https://ai.google.dev/gemini-api/docs/thinking
> - https://ai.google.dev/gemini-api/docs/media-resolution

## Table of Contents
1. [Core API Reference](#1-core-api-reference)
2. [Code Execution Specifics](#2-code-execution-specifics)
3. [Thinking Configuration](#3-thinking-configuration)
4. [Image Processing](#4-image-processing)
5. [Response Parsing](#5-response-parsing)
6. [Advanced Patterns](#6-advanced-patterns)
7. [Limitations and Gotchas](#7-limitations-and-gotchas)

---

## 1. Core API Reference

### Model IDs

| Model | ID | Status | Release |
|-------|-----|--------|---------|
| Gemini 3 Flash | `gemini-3-flash-preview` | Preview | December 2025 |
| Gemini 3 Pro | `gemini-3-pro-preview` | Preview | December 2025 |
| Gemini 3 Pro Image | `gemini-3-pro-image-preview` | Preview | December 2025 |

### Token Limits

| Specification | Value |
|---------------|-------|
| Maximum input tokens | 1,048,576 (1M) |
| Maximum output tokens | 65,536 (64K) |
| Knowledge cutoff | January 2025 |

### Configuration Parameters

```python
from google import genai
from google.genai import types

client = genai.Client(api_key="YOUR_API_KEY")

response = client.models.generate_content(
    model="gemini-3-flash-preview",
    contents=[prompt],
    config=types.GenerateContentConfig(
        # Temperature: 0.0-2.0 (default 1.0)
        temperature=1.0,

        # Top-P: 0.0-1.0 (default 0.95)
        top_p=0.95,

        # Top-K: fixed at 64
        # top_k=64,

        # Candidate count: 1-8 (default 1)
        candidate_count=1,

        # Maximum output tokens
        max_output_tokens=8192,

        # Thinking configuration
        thinking_config=types.ThinkingConfig(
            thinking_level="high"  # minimal, low, medium, high
        ),

        # Media resolution
        media_resolution=types.MediaResolution.MEDIA_RESOLUTION_HIGH,

        # Tools
        tools=[types.Tool(code_execution=types.ToolCodeExecution)],

        # Structured output
        response_mime_type="application/json",
        response_json_schema=MySchema.model_json_schema(),
    ),
)
```

### Response Object Structure

```python
# GenerateContentResponse structure
response.text                          # Combined text output
response.candidates[0].content.parts   # List of Part objects
response.candidates[0].finish_reason   # Completion reason
response.usage_metadata.prompt_token_count      # Input tokens
response.usage_metadata.candidates_token_count  # Output tokens
response.usage_metadata.total_token_count       # Total tokens
response.usage_metadata.thoughts_token_count    # Thinking tokens (if applicable)
```

### Error Codes and Handling

| HTTP Code | Status | Cause | Solution |
|-----------|--------|-------|----------|
| 400 | INVALID_ARGUMENT | Malformed request body | Verify request format; ensure API version compatibility |
| 400 | FAILED_PRECONDITION | Free tier unavailable in region | Enable paid plan via Google AI Studio |
| 403 | PERMISSION_DENIED | Invalid API key | Verify API key; use proper authentication for tuned models |
| 404 | NOT_FOUND | Resource not found | Validate all request parameters |
| 429 | RESOURCE_EXHAUSTED | Rate limit exceeded | Implement exponential backoff; request quota increase |
| 500 | INTERNAL | Backend error | Reduce context; retry; report issue |
| 503 | UNAVAILABLE | Service overloaded | Switch models temporarily; retry |
| 504 | DEADLINE_EXCEEDED | Request timeout | Increase client timeout setting |

**Error Handling Pattern:**

```python
import time
from google.api_core.exceptions import ResourceExhausted, InternalServerError

def generate_with_retry(client, model, contents, config, max_retries=5):
    for attempt in range(max_retries):
        try:
            return client.models.generate_content(
                model=model,
                contents=contents,
                config=config
            )
        except ResourceExhausted as e:
            wait_time = 2 ** attempt
            print(f"Rate limited. Waiting {wait_time}s...")
            time.sleep(wait_time)
        except InternalServerError as e:
            if attempt < max_retries - 1:
                time.sleep(2 ** attempt)
            else:
                raise
    raise Exception("Max retries exceeded")
```

### Rate Limits

| Tier | Qualification | RPM (Flash) | TPM |
|------|--------------|-------------|-----|
| Free | Eligible countries | 15 | 250,000 |
| Tier 1 | Billing account linked | 150-300 | Higher |
| Tier 2 | >$250 spend + 30 days | Higher | Higher |
| Tier 3 | >$1,000 spend + 30 days | Highest | Highest |

**Note:** Rate limits are per project, not per API key. RPD (requests per day) resets at midnight Pacific Time.

### Pricing (January 2026)

| Model | Input (per 1M tokens) | Output (per 1M tokens) |
|-------|----------------------|------------------------|
| Gemini 3 Flash Preview | $0.50 (text/image/video), $1.00 (audio) | $3.00 |
| Gemini 3 Pro Preview | $2.00 (<=200K), $4.00 (>200K) | $12.00 (<=200K), $18.00 (>200K) |
| Gemini 2.5 Flash | $0.30 (text/image/video), $1.00 (audio) | $2.50 |
| Gemini 2.5 Flash (with thinking) | Same as above | $3.50 |

**Cost Optimization:**
- Context Caching: Up to 90% reduction for repeated content
- Batch API: 50% discount on standard rates

---

## 2. Code Execution Specifics

### Enabling Code Execution

```python
from google import genai
from google.genai import types

client = genai.Client(api_key="YOUR_API_KEY")

response = client.models.generate_content(
    model="gemini-3-flash-preview",
    contents=["Calculate the factorial of 20"],
    config=types.GenerateContentConfig(
        tools=[types.Tool(code_execution=types.ToolCodeExecution)]
    ),
)
```

**REST API:**

```bash
curl -X POST "https://generativelanguage.googleapis.com/v1beta/models/gemini-3-flash-preview:generateContent" \
  -H "x-goog-api-key: $GEMINI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [{"parts": [{"text": "Calculate factorial of 20"}]}],
    "tools": [{"code_execution": {}}]
  }'
```

### Available Python Libraries in Sandbox

The code execution sandbox includes 50+ pre-installed packages:

| Category | Libraries |
|----------|-----------|
| **Data Science** | pandas, numpy, scipy, scikit-learn, statsmodels |
| **Visualization** | matplotlib, seaborn, altair |
| **Math/Science** | sympy, mpmath, tensorflow |
| **Image Processing** | pillow (PIL), opencv-python (cv2), imageio |
| **Document Handling** | python-docx, python-pptx, PyPDF2, reportlab, pdfminer |
| **File Formats** | openpyxl, xlrd, lxml, striprtf, jinja2 |
| **Utilities** | chess, geopandas, joblib, jsonschema, tabulate, toolz |
| **Core** | packaging, protobuf, pyparsing, python-dateutil, six |

**Important:** You cannot install your own libraries. Only matplotlib is supported for graph rendering.

### Execution Constraints

| Constraint | Value |
|------------|-------|
| Maximum execution time | 30 seconds |
| Maximum retry attempts | 5 times (automatic on error) |
| File input limit | Constrained by model token window (~2MB for text) |
| Language support | Python only |
| Output types | Text, matplotlib graphs (PNG) |

### File System Access

- **Cannot** save files to persistent storage
- **Cannot** return media files directly (only via matplotlib)
- **Cannot** use file URIs as input/output
- **Can** process inline file data for these formats: `.cpp`, `.csv`, `.java`, `.jpeg`, `.js`, `.png`, `.py`, `.ts`, `.xml`

### Image Manipulation Capabilities (Agentic Vision)

Gemini 3 Flash with code execution can:
- Crop and zoom into image regions
- Rotate images
- Add annotations (bounding boxes, arrows, labels)
- Perform visual math/calculations
- Generate matplotlib visualizations from image data

**Example - Agentic Vision:**

```python
from google import genai
from google.genai import types
import requests
from PIL import Image
import io

client = genai.Client(api_key="YOUR_API_KEY")

# Load image
image_url = "https://example.com/complex-diagram.jpg"
image_bytes = requests.get(image_url).content
image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")

response = client.models.generate_content(
    model="gemini-3-flash-preview",
    contents=[
        image_part,
        "Zoom into the bottom-right corner and count the number of items shown."
    ],
    config=types.GenerateContentConfig(
        tools=[types.Tool(code_execution=types.ToolCodeExecution)],
        thinking_config=types.ThinkingConfig(thinking_level="high"),
    ),
)

# Parse response with potential image outputs
for part in response.candidates[0].content.parts:
    if part.text is not None:
        print(part.text)
    if part.executable_code is not None:
        print("Code executed:")
        print(part.executable_code.code)
    if part.code_execution_result is not None:
        print("Result:", part.code_execution_result.output)
    if part.as_image() is not None:
        img = Image.open(io.BytesIO(part.as_image().image_bytes))
        img.show()
```

---

## 3. Thinking Configuration

### Thinking Levels (Gemini 3)

| Level | Description | Use Cases |
|-------|-------------|-----------|
| `minimal` | Matches "no thinking" for most queries (Flash only) | Fact retrieval, simple classification |
| `low` | Minimizes latency and cost | Simple instruction following, chat, high-throughput apps |
| `medium` | Balanced reasoning (Flash only) | Comparison tasks, moderate complexity |
| `high` (default) | Maximizes reasoning depth | Complex math, coding challenges, multi-step reasoning |

**Note:** Gemini 3 Pro only supports `low` and `high`. You cannot fully disable thinking on Gemini 3 Pro.

### Configuration Examples

**Python:**

```python
from google import genai
from google.genai import types

client = genai.Client(api_key="YOUR_API_KEY")

# High thinking for complex tasks
response = client.models.generate_content(
    model="gemini-3-flash-preview",
    contents=["Solve this differential equation: dy/dx = 3x^2 + 2x - 5"],
    config=types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(
            thinking_level="high"
        )
    ),
)

# Minimal thinking for simple tasks
response = client.models.generate_content(
    model="gemini-3-flash-preview",
    contents=["What is the capital of France?"],
    config=types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(
            thinking_level="minimal"
        )
    ),
)
```

**JavaScript:**

```javascript
import { GoogleGenAI } from '@google/genai';

const ai = new GoogleGenAI({ apiKey: 'YOUR_API_KEY' });

const response = await ai.models.generateContent({
    model: 'gemini-3-flash-preview',
    contents: ['Complex reasoning task here'],
    config: {
        thinkingConfig: {
            thinkingLevel: 'high'
        }
    }
});
```

### Legacy thinkingBudget (Gemini 2.5)

For Gemini 2.5 models, use `thinkingBudget` instead:

| Model | Default | Range | Disable | Dynamic |
|-------|---------|-------|---------|---------|
| 2.5 Pro | Dynamic | 128-32,768 | N/A | -1 |
| 2.5 Flash | Dynamic | 0-24,576 | 0 | -1 |
| 2.5 Flash-Lite | No thinking | 512-24,576 | 0 | -1 |

```python
# Gemini 2.5 thinking budget
response = client.models.generate_content(
    model="gemini-2.5-flash",
    contents=["Your prompt"],
    config=types.GenerateContentConfig(
        thinking_config=types.ThinkingConfig(
            thinking_budget=1024  # Token budget
        )
    ),
)
```

**Warning:** Do not use both `thinking_level` and `thinking_budget` in the same request (returns 400 error).

### Impact on Latency and Cost

- **Latency:** Higher thinking levels significantly increase time to first token
- **Cost:** Thinking tokens are billed as output tokens
- **Token counting:** Access via `response.usage_metadata.thoughts_token_count`

---

## 4. Image Processing

### Supported Formats and Sizes

| Format | Max File Size (Inline/Console) | Max File Size (Cloud Storage) |
|--------|-------------------------------|------------------------------|
| PNG | 7 MB | 30 MB |
| JPEG | 7 MB | 30 MB |
| WebP | 7 MB | 30 MB |
| HEIC | 7 MB | 30 MB |
| HEIF | 7 MB | 30 MB |

**Limits:**
- Maximum images per prompt: 900
- Default resolution tokens: 1,120 per image

### media_resolution Settings

| Resolution | Image Tokens | Video Tokens/Frame | PDF Tokens |
|------------|--------------|-------------------|------------|
| UNSPECIFIED (default) | 1,120 | 70 | 560 |
| LOW | 280 | 70 | 280 + native text |
| MEDIUM | 560 | 70 | 560 + native text |
| HIGH | 1,120 | 280 | 1,120 + native text |
| ULTRA_HIGH (per-part only) | 2,240 | N/A | N/A |

### Sending Images

**Single Image:**

```python
from google import genai
from google.genai import types

client = genai.Client(api_key="YOUR_API_KEY")

# From bytes
with open("image.jpg", "rb") as f:
    image_bytes = f.read()

image_part = types.Part.from_bytes(
    data=image_bytes,
    mime_type="image/jpeg"
)

response = client.models.generate_content(
    model="gemini-3-flash-preview",
    contents=["Describe this image:", image_part],
)
```

**Multiple Images with Different Resolutions (Gemini 3 only):**

```python
from google import genai
from google.genai import types

client = genai.Client(http_options={'api_version': 'v1alpha'})

# High resolution for complex diagram
image_part_high = types.Part.from_bytes(
    data=diagram_bytes,
    mime_type="image/jpeg",
    media_resolution=types.MediaResolution.MEDIA_RESOLUTION_HIGH
)

# Low resolution for simple context image
image_part_low = types.Part.from_bytes(
    data=context_bytes,
    mime_type="image/jpeg",
    media_resolution=types.MediaResolution.MEDIA_RESOLUTION_LOW
)

response = client.models.generate_content(
    model="gemini-3-flash-preview",
    contents=[
        "Compare these images:",
        image_part_high,
        image_part_low
    ],
)
```

**Global Resolution Setting:**

```python
config = types.GenerateContentConfig(
    media_resolution=types.MediaResolution.MEDIA_RESOLUTION_HIGH
)
```

### Token Counting for Images

```python
# Count tokens before sending
token_count = client.models.count_tokens(
    model="gemini-3-flash-preview",
    contents=["Describe this:", image_part]
)
print(f"Total tokens: {token_count.total_tokens}")
```

**Tokenization Rules:**
- Images <= 384px in both dimensions: 258 tokens (Gemini 2.x) or resolution-dependent (Gemini 3)
- Larger images: Cropped/scaled into 768x768 tiles, each tile = 258 tokens
- Video: 263 tokens per second
- Audio: 32 tokens per second

---

## 5. Response Parsing

### Part Types Returned

```python
for part in response.candidates[0].content.parts:
    # Text content
    if part.text is not None:
        print("Text:", part.text)

    # Executable code (before execution)
    if part.executable_code is not None:
        print("Language:", part.executable_code.language)  # "PYTHON"
        print("Code:", part.executable_code.code)

    # Code execution result
    if part.code_execution_result is not None:
        print("Outcome:", part.code_execution_result.outcome)  # "OUTCOME_OK"
        print("Output:", part.code_execution_result.output)

    # Inline data (images from matplotlib)
    if part.inline_data is not None:
        print("MIME type:", part.inline_data.mime_type)
        # Access raw bytes: part.inline_data.data

    # Helper method for images
    if part.as_image() is not None:
        img_bytes = part.as_image().image_bytes
        # Process with PIL, save to file, etc.

    # Thought signature (for multi-turn)
    if hasattr(part, 'thought_signature') and part.thought_signature:
        # Must be passed back in subsequent requests
        pass
```

### Extracting Code and Results

```python
def extract_code_execution(response):
    """Extract all code execution parts from response."""
    results = {
        "text": [],
        "code_blocks": [],
        "execution_results": [],
        "images": []
    }

    for part in response.candidates[0].content.parts:
        if part.text is not None:
            results["text"].append(part.text)
        if part.executable_code is not None:
            results["code_blocks"].append({
                "language": part.executable_code.language,
                "code": part.executable_code.code
            })
        if part.code_execution_result is not None:
            results["execution_results"].append({
                "outcome": part.code_execution_result.outcome,
                "output": part.code_execution_result.output
            })
        if part.inline_data is not None:
            results["images"].append({
                "mime_type": part.inline_data.mime_type,
                "data": part.inline_data.data
            })

    return results
```

### Handling Multi-Turn with Thought Signatures

Thought signatures are encrypted representations of internal reasoning that must be returned exactly as received:

```python
# First turn
response1 = client.models.generate_content(
    model="gemini-3-flash-preview",
    contents=[{"role": "user", "parts": [{"text": "Analyze this data..."}]}],
    config=config
)

# Build history preserving thought signatures
history = []
for part in response1.candidates[0].content.parts:
    history.append(part)

# Second turn - signatures handled automatically by SDK
response2 = client.models.generate_content(
    model="gemini-3-flash-preview",
    contents=[
        {"role": "user", "parts": [{"text": "Initial prompt"}]},
        {"role": "model", "parts": history},
        {"role": "user", "parts": [{"text": "Follow-up question"}]}
    ],
    config=config
)
```

**Note:** If using official SDKs with standard chat history, thought signatures are handled automatically.

---

## 6. Advanced Patterns

### Streaming with Code Execution

```python
from google import genai
from google.genai import types

client = genai.Client(api_key="YOUR_API_KEY")

# Streaming response
stream = client.models.generate_content_stream(
    model="gemini-3-flash-preview",
    contents=["Generate a chart showing prime numbers up to 100"],
    config=types.GenerateContentConfig(
        tools=[types.Tool(code_execution=types.ToolCodeExecution)]
    ),
)

# Accumulate parts by type
current_text = ""
current_code = ""
current_result = ""

for chunk in stream:
    for part in chunk.candidates[0].content.parts:
        if part.text is not None:
            current_text += part.text
            print(part.text, end="", flush=True)
        if part.executable_code is not None:
            current_code = part.executable_code.code
        if part.code_execution_result is not None:
            current_result = part.code_execution_result.output
        if part.inline_data is not None:
            # Handle inline image data
            pass
```

### Structured Output + Code Execution

```python
from pydantic import BaseModel, Field
from typing import List

class AnalysisResult(BaseModel):
    summary: str = Field(description="Brief summary of findings")
    data_points: List[float] = Field(description="Key numeric values")
    confidence: float = Field(description="Confidence score 0-1")

response = client.models.generate_content(
    model="gemini-3-flash-preview",
    contents=["Analyze this CSV data and extract statistics", csv_part],
    config=types.GenerateContentConfig(
        tools=[types.Tool(code_execution=types.ToolCodeExecution)],
        response_mime_type="application/json",
        response_json_schema=AnalysisResult.model_json_schema(),
    ),
)

# Parse structured result
import json
result = AnalysisResult(**json.loads(response.text))
```

### Chaining Multiple Code Executions

The model automatically chains code executions within a single request (up to 5 retries). For explicit multi-step workflows:

```python
def multi_step_analysis(client, data_file):
    """Execute multi-step analysis with code execution."""

    # Step 1: Load and clean data
    step1 = client.models.generate_content(
        model="gemini-3-flash-preview",
        contents=[
            data_file,
            "Load this data, clean it, and describe the structure. "
            "Save cleaned data to a variable called 'clean_df'."
        ],
        config=types.GenerateContentConfig(
            tools=[types.Tool(code_execution=types.ToolCodeExecution)]
        ),
    )

    # Build conversation history
    history = [
        {"role": "user", "parts": [data_file, {"text": "Load and clean..."}]},
        {"role": "model", "parts": step1.candidates[0].content.parts}
    ]

    # Step 2: Analysis (continues from previous context)
    step2 = client.models.generate_content(
        model="gemini-3-flash-preview",
        contents=history + [
            {"role": "user", "parts": [{"text": "Now perform statistical analysis on clean_df"}]}
        ],
        config=types.GenerateContentConfig(
            tools=[types.Tool(code_execution=types.ToolCodeExecution)]
        ),
    )

    return step2
```

### Error Recovery Pattern

```python
def robust_code_execution(client, prompt, max_attempts=3):
    """Execute with error recovery for code execution failures."""

    for attempt in range(max_attempts):
        response = client.models.generate_content(
            model="gemini-3-flash-preview",
            contents=[prompt],
            config=types.GenerateContentConfig(
                tools=[types.Tool(code_execution=types.ToolCodeExecution)]
            ),
        )

        # Check for execution errors
        for part in response.candidates[0].content.parts:
            if part.code_execution_result is not None:
                if "error" in part.code_execution_result.output.lower():
                    if attempt < max_attempts - 1:
                        # Modify prompt to address error
                        prompt = f"""
                        Previous attempt failed with error:
                        {part.code_execution_result.output}

                        Please fix the code and try again. Original task:
                        {prompt}
                        """
                        continue

        return response

    raise Exception("Max code execution attempts exceeded")
```

---

## 7. Limitations and Gotchas

### Known Issues

1. **Date Confusion Bug**: The model sometimes insists it's 2024 (inherited from training data)

2. **Token Usage Variability**: On complex reasoning tasks, token usage can more than double compared to simpler queries

3. **Hallucination Rate**: For applications requiring factual reliability, Gemini 3 Flash may invent answers - validate critical outputs

4. **No Image Segmentation**: Not supported yet

5. **Thought Signatures Required**: Even at `minimal` thinking level, omitting signatures degrades quality. For image generation/editing, missing signatures return 400 errors

### Code Execution Limitations

- **Python only**: Cannot execute other languages
- **No file persistence**: Cannot save files to disk
- **No network access**: Cannot make HTTP requests from sandbox
- **No custom packages**: Limited to pre-installed libraries
- **30-second timeout**: Long-running computations will fail
- **Output regression**: Enabling code execution may degrade other outputs (e.g., creative writing)

### Things That Don't Work as Expected

1. **File URIs**: Code execution doesn't support file URIs as input/output - use inline bytes

2. **Media file output**: Cannot return generated media files directly (only matplotlib PNG charts)

3. **thinking_budget + thinking_level**: Using both in the same request returns 400 error

4. **Preview model stability**: "Pre-GA products and features are available 'as is' and might have limited support"

5. **Rate limits on preview models**: More restricted than stable models; "specified rate limits are not guaranteed"

### Best Practices

```python
# DO: Use appropriate thinking level for task complexity
# Simple task
config_simple = types.GenerateContentConfig(
    thinking_config=types.ThinkingConfig(thinking_level="minimal")
)

# Complex task
config_complex = types.GenerateContentConfig(
    thinking_config=types.ThinkingConfig(thinking_level="high"),
    tools=[types.Tool(code_execution=types.ToolCodeExecution)]
)

# DO: Set media resolution appropriately
# Save 75% tokens on simple images
config_efficient = types.GenerateContentConfig(
    media_resolution=types.MediaResolution.MEDIA_RESOLUTION_LOW
)

# DO: Implement proper error handling with backoff
# DO: Monitor token usage in production
# DO: Validate structured outputs semantically

# DON'T: Use code execution for creative writing tasks
# DON'T: Expect file persistence between requests
# DON'T: Rely on exact reproducibility with thinking enabled
```

### Token Counting Recommendations

```python
# Always count tokens before large requests
token_count = client.models.count_tokens(
    model="gemini-3-flash-preview",
    contents=contents
)

if token_count.total_tokens > 900000:  # Near 1M limit
    print("Warning: Approaching context limit")
    # Consider reducing media resolution or splitting request
```

---

## Quick Reference

```python
from google import genai
from google.genai import types

client = genai.Client()  # Uses GEMINI_API_KEY env var

# Agentic Vision with code execution
response = client.models.generate_content(
    model="gemini-3-flash-preview",
    contents=[image_part, "Analyze and annotate this image"],
    config=types.GenerateContentConfig(
        tools=[types.Tool(code_execution=types.ToolCodeExecution)],
        thinking_config=types.ThinkingConfig(thinking_level="high"),
        media_resolution=types.MediaResolution.MEDIA_RESOLUTION_HIGH,
    ),
)

# Process response
for part in response.candidates[0].content.parts:
    if part.text:
        print(part.text)
    if part.executable_code:
        print(part.executable_code.code)
    if part.code_execution_result:
        print(part.code_execution_result.output)
    if part.as_image():
        # Annotated image returned
        pass
```

---

## Sources

- [Gemini Models Overview](https://ai.google.dev/gemini-api/docs/models)
- [Code Execution Documentation](https://ai.google.dev/gemini-api/docs/code-execution)
- [Gemini 3 Developer Guide](https://ai.google.dev/gemini-api/docs/gemini-3)
- [Thinking Configuration](https://ai.google.dev/gemini-api/docs/thinking)
- [Media Resolution Settings](https://ai.google.dev/gemini-api/docs/media-resolution)
- [Structured Outputs](https://ai.google.dev/gemini-api/docs/structured-output)
- [Token Counting](https://ai.google.dev/gemini-api/docs/tokens)
- [Rate Limits](https://ai.google.dev/gemini-api/docs/rate-limits)
- [API Pricing](https://ai.google.dev/gemini-api/docs/pricing)
- [Troubleshooting Guide](https://ai.google.dev/gemini-api/docs/troubleshooting)
