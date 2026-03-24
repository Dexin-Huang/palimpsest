# Nano Banana Pro (Gemini 3 Pro Image) - Complete API Documentation

> Documentation fetched: 2026-01-29
> Sources:
> - https://ai.google.dev/gemini-api/docs/image-generation
> - https://ai.google.dev/gemini-api/docs/gemini-3
> - https://deepmind.google/models/gemini-image/
> - https://ai.google.dev/gemini-api/docs/thought-signatures

## Executive Summary

**Nano Banana Pro** is Google's codename for the **Gemini 3 Pro Image** model (`gemini-3-pro-image-preview`), the most advanced native multimodal image generation and editing model in the Gemini family. It combines state-of-the-art reasoning ("Thinking") capabilities with professional-grade image generation, supporting up to 4K resolution output with industry-leading text rendering.

---

## Table of Contents
1. [Core API Reference](#1-core-api-reference)
2. [Image Generation Parameters](#2-image-generation-parameters)
3. [Input Modes](#3-input-modes)
4. [Search Grounding](#4-search-grounding)
5. [Thought Signatures](#5-thought-signatures)
6. [Advanced Patterns](#6-advanced-patterns)
7. [Pricing and Limits](#7-pricing-and-limits)
8. [Comparison with Other Models](#8-comparison-with-other-models)

---

## 1. Core API Reference

### Model IDs

| Model | ID | Stage | Description |
|-------|-----|-------|-------------|
| **Nano Banana Pro** | `gemini-3-pro-image-preview` | Public Preview | Professional asset production, advanced reasoning |
| **Nano Banana** | `gemini-2.5-flash-image` | Stable | Speed-optimized, high-volume tasks |

**Launch Date:** November 20, 2025
**Knowledge Cutoff:** January 2025

### Token Limits

| Parameter | Value |
|-----------|-------|
| Maximum input tokens | 65,536 |
| Maximum output tokens | 32,768 |
| Maximum context window | 65,536 tokens |

### Generation Configuration Parameters

```python
from google.genai import types

config = types.GenerateContentConfig(
    # Required for image generation
    response_modalities=['TEXT', 'IMAGE'],  # Must include both

    # Image-specific configuration
    image_config=types.ImageConfig(
        aspect_ratio="16:9",   # See supported values below
        image_size="2K"        # "1K", "2K", or "4K"
    ),

    # Standard generation parameters
    temperature=1.0,           # Range: 0.0-2.0 (default 1.0 recommended)
    top_p=0.95,               # Range: 0.0-1.0 (default 0.95)
    top_k=64,                 # Fixed at 64
    candidate_count=1,        # Always 1 for image generation

    # Optional tools
    tools=[{"google_search": {}}],  # Enable grounding

    # Thinking configuration (Gemini 3)
    thinking_level="high"     # "low", "medium", "high", "minimal"
)
```

### Response Object Structure

```json
{
  "candidates": [{
    "content": {
      "parts": [
        {
          "text": "Here is the generated image...",
          "thoughtSignature": "encrypted_signature_string"
        },
        {
          "inlineData": {
            "mimeType": "image/png",
            "data": "base64_encoded_image_data..."
          },
          "thoughtSignature": "encrypted_signature_string"
        }
      ],
      "role": "model"
    },
    "finishReason": "STOP",
    "groundingMetadata": {
      "webSearchQueries": ["query1", "query2"],
      "groundingChunks": [
        {"web": {"uri": "https://...", "title": "Source Title"}}
      ],
      "groundingSupports": [...],
      "searchEntryPoint": {"renderedContent": "<html>...</html>"}
    }
  }],
  "usageMetadata": {
    "promptTokenCount": 150,
    "candidatesTokenCount": 1200,
    "totalTokenCount": 1350
  }
}
```

### Error Codes

| Code | Name | Description | Resolution |
|------|------|-------------|------------|
| 400 | Bad Request | Malformed request, invalid parameters, missing thought signature | Check request structure, ensure thought signatures are passed back |
| 403 | Forbidden | Invalid API key, permission denied | Verify API key, check billing status |
| 429 | Resource Exhausted | Rate limit or quota exceeded | Implement exponential backoff, upgrade tier |
| 500 | Internal Error | Server-side failure | Retry with backoff |
| 503 | Model Overloaded | Capacity constraints | Retry later, use batch API |

---

## 2. Image Generation Parameters

### Aspect Ratios

| Value | Use Case |
|-------|----------|
| `"1:1"` | Square images, social media posts |
| `"2:3"` | Portrait photos |
| `"3:2"` | Landscape photos |
| `"3:4"` | Portrait format |
| `"4:3"` | Standard landscape |
| `"4:5"` | Instagram portrait |
| `"5:4"` | Landscape variant |
| `"9:16"` | Vertical video, stories |
| `"16:9"` | Widescreen, presentations |
| `"21:9"` | Ultrawide, cinematic |

### Resolution Options

| Setting | Approximate Dimensions | Tokens | Cost per Image |
|---------|----------------------|--------|----------------|
| `"1K"` | ~1024x1024 | ~1,120 | ~$0.134 |
| `"2K"` | ~2048x2048 | ~1,120 | ~$0.134 |
| `"4K"` | ~4096x4096 | ~2,000 | ~$0.24 |

**Note:** Use uppercase only (`"1K"`, `"2K"`, `"4K"`).

### Input Image Specifications

| Parameter | Limit |
|-----------|-------|
| Maximum images per prompt | 14 |
| Object reference images | Up to 6 |
| Human reference images | Up to 5 (for character consistency) |
| Maximum file size (inline) | 7 MB per image |
| Maximum file size (GCS) | 30 MB per image |
| Total request size | 20 MB |
| Supported formats | PNG, JPEG, WebP, HEIC, HEIF |

---

## 3. Input Modes

### Text-to-Image Generation

```python
from google import genai
from google.genai import types

client = genai.Client()

response = client.models.generate_content(
    model="gemini-3-pro-image-preview",
    contents="A serene Japanese garden with a koi pond at sunset, photorealistic",
    config=types.GenerateContentConfig(
        response_modalities=['TEXT', 'IMAGE'],
        image_config=types.ImageConfig(
            aspect_ratio="16:9",
            image_size="2K"
        )
    )
)

# Extract and save image
for part in response.parts:
    if part.text:
        print(part.text)
    elif image := part.as_image():
        image.save("japanese_garden.png")
```

### Image-to-Image Editing

```python
from google import genai
from google.genai import types
from pathlib import Path
import base64

client = genai.Client()

# Load reference image
image_path = Path("input_image.jpg")
image_data = base64.b64encode(image_path.read_bytes()).decode('utf-8')

response = client.models.generate_content(
    model="gemini-3-pro-image-preview",
    contents=[
        types.Part(
            inline_data=types.Blob(
                mime_type="image/jpeg",
                data=image_data
            )
        ),
        types.Part(text="Change the sky to a dramatic sunset with orange and purple colors")
    ],
    config=types.GenerateContentConfig(
        response_modalities=['TEXT', 'IMAGE'],
        image_config=types.ImageConfig(aspect_ratio="16:9")
    )
)
```

### Multi-Turn Conversational Editing

```python
from google import genai
from google.genai import types

client = genai.Client()

# Start a chat session (SDK handles thought signatures automatically)
chat = client.chats.create(model="gemini-3-pro-image-preview")

# Initial generation
response1 = chat.send_message(
    "Create a cozy coffee shop interior with warm lighting",
    config=types.GenerateContentConfig(
        response_modalities=['TEXT', 'IMAGE'],
        image_config=types.ImageConfig(aspect_ratio="16:9", image_size="2K")
    )
)

# Iterative refinement - SDK preserves thought signatures
response2 = chat.send_message(
    "Add a barista behind the counter making latte art"
)

response3 = chat.send_message(
    "Make the lighting more golden and add steam rising from the cups"
)
```

### Multi-Image Composition

```python
from google import genai
from google.genai import types
import base64

client = genai.Client()

# Load multiple reference images
def load_image(path):
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode('utf-8')

response = client.models.generate_content(
    model="gemini-3-pro-image-preview",
    contents=[
        types.Part(text="Image A (pose reference):"),
        types.Part(inline_data=types.Blob(mime_type="image/jpeg", data=load_image("pose.jpg"))),
        types.Part(text="Image B (face reference):"),
        types.Part(inline_data=types.Blob(mime_type="image/jpeg", data=load_image("face.jpg"))),
        types.Part(text="Image C (background):"),
        types.Part(inline_data=types.Blob(mime_type="image/jpeg", data=load_image("background.jpg"))),
        types.Part(text="Combine these: Use the pose from Image A, the face from Image B, placed in the environment from Image C. Professional photography lighting.")
    ],
    config=types.GenerateContentConfig(
        response_modalities=['TEXT', 'IMAGE'],
        image_config=types.ImageConfig(aspect_ratio="3:2", image_size="4K")
    )
)
```

### Conversational Inpainting

```python
# Conversational inpainting with Gemini 3 Pro Image
response = client.models.generate_content(
    model="gemini-3-pro-image-preview",
    contents=[
        types.Part(inline_data=types.Blob(mime_type="image/jpeg", data=room_image)),
        types.Part(text="Remove the person standing by the window and fill with appropriate background")
    ],
    config=types.GenerateContentConfig(
        response_modalities=['TEXT', 'IMAGE']
    )
)
```

---

## 4. Search Grounding

### Enabling Google Search

```python
from google import genai
from google.genai import types

client = genai.Client()

response = client.models.generate_content(
    model="gemini-3-pro-image-preview",
    contents="Create an infographic showing today's weather forecast for Tokyo with accurate current temperatures",
    config=types.GenerateContentConfig(
        response_modalities=['TEXT', 'IMAGE'],
        image_config=types.ImageConfig(aspect_ratio="9:16"),
        tools=[{"google_search": {}}]  # Enable grounding
    )
)

# Access grounding metadata
if response.candidates[0].grounding_metadata:
    metadata = response.candidates[0].grounding_metadata
    print("Search queries used:", metadata.web_search_queries)
    for chunk in metadata.grounding_chunks:
        print(f"Source: {chunk.web.title} - {chunk.web.uri}")
```

### What Grounding Enables

- **Real-time data visualization:** Current weather, stock charts, sports scores
- **Fact verification:** Accurate statistics, dates, names
- **Current events:** Recent news, live data
- **Reference imagery:** Real products, landmarks, public figures

### Grounding Limitations

- Image-based search results are NOT passed to the generation model
- Text information only from search results
- Must display search suggestions in production applications
- Additional cost per search query (see pricing section)

### cURL Example with Grounding

```bash
curl -s -X POST \
  "https://generativelanguage.googleapis.com/v1beta/models/gemini-3-pro-image-preview:generateContent" \
  -H "x-goog-api-key: $GEMINI_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "contents": [{
      "parts": [{
        "text": "Create a visualization of current Bitcoin price trends"
      }]
    }],
    "tools": [{"google_search": {}}],
    "generationConfig": {
      "responseModalities": ["TEXT", "IMAGE"],
      "imageConfig": {"aspectRatio": "16:9", "imageSize": "2K"}
    }
  }'
```

---

## 5. Thought Signatures

### What Are Thought Signatures?

Thought signatures are **encrypted representations of the model's internal reasoning process**. They preserve context across multi-turn interactions, enabling coherent iterative refinement of generated images.

### When They Appear

Signatures appear in response parts:
- On the first part after the thoughts (text or inlineData)
- On every subsequent `inlineData` part

```json
{
  "parts": [
    {
      "text": "Here is your image...",
      "thoughtSignature": "aGVsbG8gd29ybGQ..."
    },
    {
      "inlineData": {
        "mimeType": "image/png",
        "data": "..."
      },
      "thoughtSignature": "YW5vdGhlciBzaWdu..."
    }
  ]
}
```

### When They Are Required

| Scenario | Requirement |
|----------|-------------|
| Multi-turn image editing | **Mandatory** - 400 error without them |
| Function calling in Gemini 3 | **Mandatory** - validation error without them |
| Sequential function calls | **Mandatory** |
| Text/reasoning parts | Recommended but not strictly validated |

### SDK Handling

**Official SDKs (Python, Node.js, Java):** Thought signatures are handled **automatically** when using the chat feature or standard conversation history.

```python
# Automatic handling with chat
chat = client.chats.create(model="gemini-3-pro-image-preview")
response1 = chat.send_message("Generate a logo")
response2 = chat.send_message("Make it more minimalist")  # Signatures handled
```

### Manual Handling (REST API)

```python
# Manual signature preservation
history = []

# First turn
response1 = client.models.generate_content(
    model="gemini-3-pro-image-preview",
    contents=[{"role": "user", "parts": [{"text": "Create a logo"}]}],
    config=config
)

# Extract and preserve signatures
model_parts = []
for part in response1.candidates[0].content.parts:
    part_dict = {}
    if hasattr(part, 'text') and part.text:
        part_dict['text'] = part.text
    if hasattr(part, 'inline_data') and part.inline_data:
        part_dict['inlineData'] = {
            'mimeType': part.inline_data.mime_type,
            'data': part.inline_data.data
        }
    if hasattr(part, 'thought_signature') and part.thought_signature:
        part_dict['thoughtSignature'] = part.thought_signature
    model_parts.append(part_dict)

# Second turn with preserved signatures
response2 = client.models.generate_content(
    model="gemini-3-pro-image-preview",
    contents=[
        {"role": "user", "parts": [{"text": "Create a logo"}]},
        {"role": "model", "parts": model_parts},  # Include signatures
        {"role": "user", "parts": [{"text": "Make it minimalist"}]}
    ],
    config=config
)
```

---

## 6. Advanced Patterns

### Iterative Refinement Workflow

```python
from google import genai
from google.genai import types

client = genai.Client()
chat = client.chats.create(model="gemini-3-pro-image-preview")

config = types.GenerateContentConfig(
    response_modalities=['TEXT', 'IMAGE'],
    image_config=types.ImageConfig(aspect_ratio="1:1", image_size="2K")
)

# Step 1: Initial concept
response = chat.send_message(
    "Create a vintage travel poster for Paris with Art Deco styling",
    config=config
)

# Step 2: Composition refinement
response = chat.send_message(
    "Move the Eiffel Tower more to the left and add dramatic sunset colors"
)

# Step 3: Typography
response = chat.send_message(
    "Add elegant text at the top saying 'PARIS' in gold Art Deco lettering"
)

# Step 4: Final polish - upgrade to 4K
final_config = types.GenerateContentConfig(
    response_modalities=['TEXT', 'IMAGE'],
    image_config=types.ImageConfig(aspect_ratio="1:1", image_size="4K")
)
response = chat.send_message(
    "Perfect. Now render the final version at highest quality",
    config=final_config
)
```

### Combining with Text Analysis

```python
from google import genai
from google.genai import types

client = genai.Client()

# Analyze an image, then generate based on analysis
analyze_response = client.models.generate_content(
    model="gemini-3-pro-preview",  # Text model for analysis
    contents=[
        types.Part(inline_data=types.Blob(mime_type="image/jpeg", data=reference_image)),
        types.Part(text="Analyze the color palette, composition style, and mood of this image. Describe in detail.")
    ]
)

style_description = analyze_response.text

# Generate new image in same style
generate_response = client.models.generate_content(
    model="gemini-3-pro-image-preview",
    contents=f"Create a new image of a mountain landscape using this exact style: {style_description}",
    config=types.GenerateContentConfig(
        response_modalities=['TEXT', 'IMAGE'],
        image_config=types.ImageConfig(aspect_ratio="16:9", image_size="2K")
    )
)
```

### Batch Generation

```python
from google import genai
from google.genai import types
import json

client = genai.Client()

# Prepare batch requests (JSONL format)
batch_requests = [
    {
        "model": "gemini-3-pro-image-preview",
        "contents": [{"parts": [{"text": "A futuristic cityscape at dawn"}]}],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": {"aspectRatio": "16:9", "imageSize": "2K"}
        }
    },
    {
        "model": "gemini-3-pro-image-preview",
        "contents": [{"parts": [{"text": "An ancient forest with bioluminescent plants"}]}],
        "generationConfig": {
            "responseModalities": ["TEXT", "IMAGE"],
            "imageConfig": {"aspectRatio": "16:9", "imageSize": "2K"}
        }
    }
]

# Write JSONL file
with open("batch_input.jsonl", "w") as f:
    for req in batch_requests:
        f.write(json.dumps(req) + "\n")

# Submit batch job (50% cost savings, ~24hr turnaround)
batch_job = client.batches.create(
    model="gemini-3-pro-image-preview",
    src="batch_input.jsonl",
    dest="batch_output.jsonl"
)
```

### Consistent Style Across Multiple Generations

```python
from google import genai
from google.genai import types

client = genai.Client()

# Define style reference
style_prompt = """
Style requirements (maintain across all images):
- Color palette: Warm earth tones (terracotta, sage green, cream)
- Lighting: Soft golden hour illumination
- Composition: Rule of thirds, minimal negative space
- Mood: Calm, organic, natural
- Texture: Subtle grain, matte finish
"""

subjects = [
    "a ceramic coffee mug",
    "a leather-bound journal",
    "a potted succulent plant",
    "a woven basket"
]

for subject in subjects:
    response = client.models.generate_content(
        model="gemini-3-pro-image-preview",
        contents=f"{style_prompt}\n\nSubject: {subject}\n\nGenerate a product photograph.",
        config=types.GenerateContentConfig(
            response_modalities=['TEXT', 'IMAGE'],
            image_config=types.ImageConfig(aspect_ratio="1:1", image_size="2K")
        )
    )
    # Save with consistent naming
    for part in response.parts:
        if image := part.as_image():
            image.save(f"{subject.replace(' ', '_')}.png")
```

---

## 7. Pricing and Limits

### Pricing Structure

| Component | Cost |
|-----------|------|
| **Input tokens (text)** | $2.00 / 1M tokens (<=200k), $4.00 / 1M (>200k) |
| **Output tokens (text)** | $12.00 / 1M tokens (<=200k), $18.00 / 1M (>200k) |
| **Image output (1K/2K)** | ~$0.134 per image |
| **Image output (4K)** | ~$0.24 per image |
| **Batch API** | 50% discount on all prices |
| **Google Search grounding** | $35 / 1,000 grounded prompts (after free tier) |

### Free Tier

- Available in Google AI Studio
- ~1,500 images daily (varies)
- 500 grounded prompts/day (search)
- Rate limits: 5-15 RPM depending on model

### Rate Limits by Tier

| Tier | Requirements | RPM | TPM | RPD |
|------|--------------|-----|-----|-----|
| **Free** | Eligible countries | 5-15 | Varies | 100 |
| **Tier 1** | Billing enabled | 300 | 1M | 1,000 |
| **Tier 2** | $250+ spend, 30+ days | 1,000 | 2M | 10,000 |
| **Tier 3** | Enterprise agreement | 4,000+ | 4M+ | Unlimited |

### Content Policy / Safety Filters

Safety thresholds can be configured:
- `BLOCK_LOW_AND_ABOVE` - Most restrictive
- `BLOCK_MEDIUM_AND_ABOVE` - Default
- `BLOCK_ONLY_HIGH` - Least restrictive
- `HARM_BLOCK_THRESHOLD_UNSPECIFIED` - Use default

All generated images include **SynthID watermark** and **C2PA metadata** for provenance.

---

## 8. Comparison with Other Models

### Feature Comparison

| Feature | Gemini 3 Pro Image | DALL-E 3 / GPT Image | Midjourney V7 | Stable Diffusion 3 |
|---------|-------------------|---------------------|---------------|-------------------|
| **Max Resolution** | 4K (4096px) | 1792x1024 | 2048x2048 | Variable |
| **Text Rendering** | Industry-leading | Good | Good | Moderate |
| **Multi-turn Editing** | Native, conversational | Via ChatGPT | Limited | No |
| **Search Grounding** | Yes, real-time data | No | No | No |
| **Reference Images** | Up to 14 | No | Image prompts | img2img |
| **Character Consistency** | Up to 5 people | Limited | Strong | Requires LoRA |
| **Native Reasoning** | Yes (Thinking) | Yes (GPT-4o) | No | No |
| **API Access** | Full REST/SDK | API available | No official API | Open source |
| **Batch Processing** | Yes, 50% discount | No | No | Self-hosted |

### Use Case Recommendations

| Use Case | Recommended Model |
|----------|-------------------|
| Text-heavy infographics | **Gemini 3 Pro Image** |
| Real-time data visualization | **Gemini 3 Pro Image** |
| Iterative design refinement | **Gemini 3 Pro Image** |
| Character-consistent series | **Gemini 3 Pro Image** or **Midjourney** |
| Artistic/emotional imagery | **Midjourney** |
| Beginner/casual use | **DALL-E 3 (ChatGPT)** |
| High-volume production | **Gemini (batch)** or **Stable Diffusion** |
| Privacy-sensitive | **Stable Diffusion (self-hosted)** |

---

## Quick Reference

```python
from google import genai
from google.genai import types

client = genai.Client()  # Uses GEMINI_API_KEY env var

response = client.models.generate_content(
    model="gemini-3-pro-image-preview",
    contents="Your prompt here",
    config=types.GenerateContentConfig(
        response_modalities=['TEXT', 'IMAGE'],
        image_config=types.ImageConfig(
            aspect_ratio="16:9",  # 1:1, 2:3, 3:2, 3:4, 4:3, 4:5, 5:4, 9:16, 16:9, 21:9
            image_size="2K"       # 1K, 2K, 4K
        ),
        tools=[{"google_search": {}}],  # Optional: enable grounding
        temperature=1.0,
        thinking_level="high"  # low, medium, high, minimal
    )
)

for part in response.parts:
    if part.text:
        print(part.text)
    elif image := part.as_image():
        image.save("output.png")
```

---

## Sources

- [Nano Banana Image Generation - Google AI for Developers](https://ai.google.dev/gemini-api/docs/image-generation)
- [Gemini 3 Developer Guide - Google AI](https://ai.google.dev/gemini-api/docs/gemini-3)
- [Thought Signatures - Google AI](https://ai.google.dev/gemini-api/docs/thought-signatures)
- [Gemini 3 Pro Image - Vertex AI Documentation](https://docs.cloud.google.com/vertex-ai/generative-ai/docs/models/gemini/3-pro-image)
- [Grounding with Google Search - Google AI](https://ai.google.dev/gemini-api/docs/google-search)
- [Gemini API Pricing - Google AI](https://ai.google.dev/gemini-api/docs/pricing)
- [Gemini API Rate Limits - Google AI](https://ai.google.dev/gemini-api/docs/rate-limits)
- [Google DeepMind - Gemini Image](https://deepmind.google/models/gemini-image/)
