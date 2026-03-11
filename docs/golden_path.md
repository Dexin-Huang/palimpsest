# Palimpsest Golden Path: Gemini Models

> Updated: 2026-01-29

## Models to Use

| Task | Model | Model ID |
|------|-------|----------|
| **Image Analysis** (OCR, transcription, annotation) | Gemini 3.1 Flash Lite + Agentic Vision | `gemini-3.1-flash-lite-preview` |
| **Image Generation** (reconstructions, visualizations) | Gemini 3.1 Flash Image | `gemini-3.1-flash-image-preview` |

---

## Agentic Vision (Gemini 3.1 Flash Lite)

For manuscript analysis with code execution capabilities.

### Basic Setup

```python
from google import genai
from google.genai import types

client = genai.Client()  # Uses GEMINI_API_KEY env var

def analyze_manuscript_page(image_path: Path, prompt: str) -> dict:
    """Analyze manuscript with Agentic Vision."""

    image_part = types.Part.from_bytes(
        data=image_path.read_bytes(),
        mime_type="image/jpeg"
    )

    response = client.models.generate_content(
        model="gemini-3.1-flash-lite-preview",
        contents=[image_part, prompt],
        config=types.GenerateContentConfig(
            tools=[types.Tool(code_execution=types.ToolCodeExecution)],
            thinking_config=types.ThinkingConfig(
                thinking_level="HIGH",  # or thinkingBudget=2048
            ),
        ),
    )

    return response
```

### Processing Response

```python
for part in response.candidates[0].content.parts:
    if part.text:
        print("Analysis:", part.text)
    if part.executable_code:
        print("Code executed:", part.executable_code.code)
    if part.code_execution_result:
        print("Result:", part.code_execution_result)
    if part.as_image():
        # Annotated image returned
        annotated_bytes = part.as_image()
```

### Key Parameters

```python
config=types.GenerateContentConfig(
    # Enable code execution for zoom/crop/annotate
    tools=[types.Tool(code_execution=types.ToolCodeExecution)],

    # Thinking level for complex analysis
    thinking_config=types.ThinkingConfig(
        thinking_level="HIGH",  # LOW, MEDIUM, HIGH
        # or: thinkingBudget=2048
    ),

    # For structured output
    response_mime_type="application/json",

    # Image processing quality
    # media_resolution="media_resolution_high",  # 1120 tokens
)
```

### Use Cases for Manuscripts

- **Zoom and inspect**: Detect small text, crop and re-examine at higher resolution
- **Annotation**: Draw bounding boxes around figures, mark text regions
- **Counting**: Count labeled figures, columns, marginalia
- **Visual verification**: Verify transcription by highlighting text regions

---

## Nano Banana Pro (Gemini 3 Pro Image)

For generating reconstructions or visualizations.

### Basic Setup

```python
def generate_reconstruction(prompt: str, reference_image: Path = None) -> bytes:
    """Generate image with Nano Banana Pro."""

    contents = []

    if reference_image:
        contents.append(types.Part.from_bytes(
            data=reference_image.read_bytes(),
            mime_type="image/jpeg"
        ))

    contents.append(prompt)

    response = client.models.generate_content(
        model="gemini-3.1-flash-image-preview",
        contents=contents,
        config=types.GenerateContentConfig(
            image_config=types.ImageConfig(
                aspect_ratio="4:3",  # or "16:9", "1:1"
                image_size="4K",     # or "1080p"
            )
        )
    )

    # Extract generated image
    for part in response.candidates[0].content.parts:
        if part.as_image():
            return part.as_image()
```

### With Search Grounding

```python
response = client.models.generate_content(
    model="gemini-3.1-flash-image-preview",
    contents="Create an infographic about medieval alchemy symbols",
    config=types.GenerateContentConfig(
        tools=[{"google_search": {}}],  # Enable grounding
        image_config=types.ImageConfig(
            aspect_ratio="16:9",
            image_size="4K"
        )
    )
)
```

### Multi-turn Editing

```python
# Initial generation
response1 = client.models.generate_content(
    model="gemini-3.1-flash-image-preview",
    contents="Reconstruct this faded manuscript text in clearer form"
)

# Refinement
response2 = client.models.generate_content(
    model="gemini-3.1-flash-image-preview",
    contents=[
        response1.candidates[0].content,
        "Make the rubric (red text) more visible"
    ]
)
```

---

## Pricing

| Model | Input | Output |
|-------|-------|--------|
| Gemini 3.1 Flash Lite | $0.25/M tokens | $1.50/M tokens |
| Gemini 3 Pro Image | $2/M tokens | $0.134/image |

---

## Environment Setup

```bash
export GEMINI_API_KEY="your-api-key-here"
export PALIMPSEST_MODEL_VISION="gemini-3.1-flash-lite-preview"
export PALIMPSEST_MODEL_RECON="gemini-3.1-flash-image-preview"
pip install google-genai
```
