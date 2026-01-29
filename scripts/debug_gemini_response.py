#!/usr/bin/env python3
"""Debug script to inspect Gemini 3 Flash Agentic Vision response structure.

NOTE: This is a debugging/reference script. For production transcription, use:
    python scripts/transcribe_page.py --image <path> --out <path>

The learnings from this script have been incorporated into:
    palimpsest/pipeline/ocr.py (AgenticVisionResponse, extract_agentic_vision_response)

This script makes a test call with code_execution enabled and prints the
complete response structure to understand:
1. What parts are returned (text, executable_code, code_execution_result, inline_data, etc.)
2. How to extract ALL text content properly
3. Why response.text might be truncated or corrupted

KEY FINDINGS:
=============
With code_execution enabled (Agentic Vision), Gemini returns a multi-part response:

Response structure:
- response.candidates[0].content.parts[] contains multiple parts:
  - Part with executable_code: Python code the model executed to crop/zoom
  - Part with code_execution_result: Output from running the code
  - Parts with inline_data: JPEG images (cropped regions) the model created
  - Part with text: The actual description/analysis

The response.text property returns ONLY the text part, which is correct behavior.
It does NOT get truncated/corrupted - the SDK warns about non-text parts.

Part types identified:
1. executable_code (ExecutableCode) - Python code with .language and .code
2. code_execution_result (CodeExecutionResult) - .outcome and .output
3. inline_data (Blob) - Images with .mime_type and .data (bytes)
4. text (str) - The actual text content

Usage:
    python scripts/debug_gemini_response.py
"""

import os
import json
from pathlib import Path
from dataclasses import dataclass
from typing import Optional, List
from dotenv import load_dotenv

# Load API key from .env
env_path = Path(__file__).parent.parent / ".env"
load_dotenv(env_path)

from google import genai
from google.genai import types


@dataclass
class AgenticVisionResponse:
    """Parsed response from Gemini with Agentic Vision (code_execution)."""
    text: str
    code_blocks: List[dict]  # [{'language': str, 'code': str}]
    code_results: List[dict]  # [{'outcome': str, 'output': str}]
    images: List[dict]  # [{'mime_type': str, 'data': bytes}]
    raw_response: object


def extract_agentic_vision_response(response) -> AgenticVisionResponse:
    """Extract all content from a Gemini response with code_execution enabled.

    Args:
        response: The raw response from client.models.generate_content()

    Returns:
        AgenticVisionResponse with all extracted parts
    """
    text_parts = []
    code_blocks = []
    code_results = []
    images = []

    if hasattr(response, 'candidates'):
        for candidate in response.candidates:
            if hasattr(candidate, 'content') and hasattr(candidate.content, 'parts'):
                for part in candidate.content.parts:
                    # Extract text parts
                    if hasattr(part, 'text') and part.text:
                        text_parts.append(part.text)

                    # Extract code blocks
                    if hasattr(part, 'executable_code') and part.executable_code:
                        code = part.executable_code
                        code_blocks.append({
                            'language': str(getattr(code, 'language', 'unknown')),
                            'code': getattr(code, 'code', '')
                        })

                    # Extract code execution results
                    if hasattr(part, 'code_execution_result') and part.code_execution_result:
                        result = part.code_execution_result
                        code_results.append({
                            'outcome': str(getattr(result, 'outcome', 'unknown')),
                            'output': getattr(result, 'output', '')
                        })

                    # Extract inline images
                    if hasattr(part, 'inline_data') and part.inline_data:
                        data = part.inline_data
                        images.append({
                            'mime_type': getattr(data, 'mime_type', 'unknown'),
                            'data': getattr(data, 'data', b'')
                        })

    return AgenticVisionResponse(
        text="\n\n".join(text_parts),
        code_blocks=code_blocks,
        code_results=code_results,
        images=images,
        raw_response=response
    )


def debug_response_structure():
    """Make a test call and inspect the full response structure."""

    # Initialize client
    client = genai.Client()
    model = "gemini-3-flash-preview"

    # Load test image
    image_path = Path(r"D:\Projects\palimpsest\projects\vatican_alchemy\images\f020r_test.jpg")
    if not image_path.exists():
        print(f"ERROR: Image not found: {image_path}")
        return

    print(f"Image: {image_path}")
    print(f"Model: {model}")
    print("=" * 80)

    # Read image
    image_bytes = image_path.read_bytes()
    image_part = types.Part.from_bytes(data=image_bytes, mime_type="image/jpeg")

    prompt = "Describe this manuscript page. Zoom in if needed."

    print(f"\nPrompt: {prompt}")
    print("=" * 80)

    # Call Gemini with code_execution enabled (Agentic Vision)
    print("\nCalling Gemini API with code_execution enabled...")
    response = client.models.generate_content(
        model=model,
        contents=[prompt, image_part],
        config=types.GenerateContentConfig(
            temperature=0.1,
            tools=[types.Tool(code_execution=types.ToolCodeExecution())],
        ),
    )

    print("\n" + "=" * 80)
    print("RESPONSE STRUCTURE DEBUG")
    print("=" * 80)

    # 1. Top-level response attributes
    print("\n--- TOP-LEVEL RESPONSE ATTRIBUTES ---")
    print(f"Type: {type(response)}")
    print(f"Dir: {[a for a in dir(response) if not a.startswith('_')]}")

    # 2. Check candidates
    print("\n--- CANDIDATES ---")
    if hasattr(response, 'candidates'):
        print(f"Number of candidates: {len(response.candidates)}")
        for i, candidate in enumerate(response.candidates):
            print(f"\n  Candidate {i}:")
            print(f"    Type: {type(candidate)}")
            print(f"    Finish reason: {getattr(candidate, 'finish_reason', 'N/A')}")
            print(f"    Safety ratings: {getattr(candidate, 'safety_ratings', 'N/A')}")

            # Check content
            if hasattr(candidate, 'content'):
                content = candidate.content
                print(f"\n    Content:")
                print(f"      Type: {type(content)}")
                print(f"      Role: {getattr(content, 'role', 'N/A')}")

                # Check parts
                if hasattr(content, 'parts'):
                    print(f"\n    Number of parts: {len(content.parts)}")
                    for j, part in enumerate(content.parts):
                        print(f"\n      Part {j}:")
                        print(f"        Type: {type(part)}")
                        print(f"        Attributes: {[a for a in dir(part) if not a.startswith('_')]}")

                        # Check for text
                        if hasattr(part, 'text') and part.text:
                            text_preview = part.text[:200] + "..." if len(part.text) > 200 else part.text
                            print(f"        text: (len={len(part.text)}) {repr(text_preview)}")

                        # Check for executable_code
                        if hasattr(part, 'executable_code') and part.executable_code:
                            code = part.executable_code
                            print(f"        executable_code:")
                            print(f"          Type: {type(code)}")
                            if hasattr(code, 'language'):
                                print(f"          Language: {code.language}")
                            if hasattr(code, 'code'):
                                code_preview = code.code[:200] + "..." if len(code.code) > 200 else code.code
                                print(f"          Code (len={len(code.code)}): {repr(code_preview)}")

                        # Check for code_execution_result
                        if hasattr(part, 'code_execution_result') and part.code_execution_result:
                            result = part.code_execution_result
                            print(f"        code_execution_result:")
                            print(f"          Type: {type(result)}")
                            if hasattr(result, 'outcome'):
                                print(f"          Outcome: {result.outcome}")
                            if hasattr(result, 'output'):
                                output_preview = result.output[:200] + "..." if len(result.output) > 200 else result.output
                                print(f"          Output (len={len(result.output)}): {repr(output_preview)}")

                        # Check for inline_data (images)
                        if hasattr(part, 'inline_data') and part.inline_data:
                            data = part.inline_data
                            print(f"        inline_data:")
                            print(f"          Type: {type(data)}")
                            if hasattr(data, 'mime_type'):
                                print(f"          MIME type: {data.mime_type}")
                            if hasattr(data, 'data'):
                                print(f"          Data length: {len(data.data)} bytes")

                        # Check for function_call
                        if hasattr(part, 'function_call') and part.function_call:
                            print(f"        function_call: {part.function_call}")

                        # Check for function_response
                        if hasattr(part, 'function_response') and part.function_response:
                            print(f"        function_response: {part.function_response}")
    else:
        print("No candidates attribute found")

    # 3. Check response.text
    print("\n--- response.text ---")
    try:
        text = response.text
        print(f"Length: {len(text)}")
        print(f"Preview (first 500 chars): {repr(text[:500])}")
        if len(text) > 500:
            print(f"... (truncated, total {len(text)} chars)")
    except Exception as e:
        print(f"ERROR accessing response.text: {type(e).__name__}: {e}")

    # 4. Proper extraction of ALL text content
    print("\n" + "=" * 80)
    print("PROPER TEXT EXTRACTION")
    print("=" * 80)

    all_text_parts = []
    all_code_parts = []
    all_code_results = []
    all_images = []

    if hasattr(response, 'candidates'):
        for candidate in response.candidates:
            if hasattr(candidate, 'content') and hasattr(candidate.content, 'parts'):
                for part in candidate.content.parts:
                    # Extract text parts
                    if hasattr(part, 'text') and part.text:
                        all_text_parts.append(part.text)

                    # Extract code parts
                    if hasattr(part, 'executable_code') and part.executable_code:
                        code = part.executable_code
                        all_code_parts.append({
                            'language': getattr(code, 'language', 'unknown'),
                            'code': getattr(code, 'code', '')
                        })

                    # Extract code execution results
                    if hasattr(part, 'code_execution_result') and part.code_execution_result:
                        result = part.code_execution_result
                        all_code_results.append({
                            'outcome': getattr(result, 'outcome', 'unknown'),
                            'output': getattr(result, 'output', '')
                        })

                    # Extract inline images
                    if hasattr(part, 'inline_data') and part.inline_data:
                        data = part.inline_data
                        all_images.append({
                            'mime_type': getattr(data, 'mime_type', 'unknown'),
                            'size_bytes': len(getattr(data, 'data', b''))
                        })

    print(f"\nFound {len(all_text_parts)} text parts")
    print(f"Found {len(all_code_parts)} code execution parts")
    print(f"Found {len(all_code_results)} code results")
    print(f"Found {len(all_images)} inline images")

    # Print all text content
    print("\n--- ALL TEXT CONTENT ---")
    full_text = "\n\n".join(all_text_parts)
    print(f"Combined length: {len(full_text)} chars")
    print(f"\nFull text:\n{full_text}")

    # Print code execution info
    if all_code_parts:
        print("\n--- CODE EXECUTION ---")
        for i, code in enumerate(all_code_parts):
            print(f"\nCode block {i} ({code['language']}):")
            print(code['code'])

    if all_code_results:
        print("\n--- CODE RESULTS ---")
        for i, result in enumerate(all_code_results):
            print(f"\nResult {i} (outcome: {result['outcome']}):")
            print(result['output'])

    if all_images:
        print("\n--- GENERATED IMAGES ---")
        for i, img in enumerate(all_images):
            print(f"  Image {i}: {img['mime_type']}, {img['size_bytes']} bytes")

    # 5. Using the helper function
    print("\n" + "=" * 80)
    print("USING extract_agentic_vision_response() FUNCTION")
    print("=" * 80)

    parsed = extract_agentic_vision_response(response)
    print(f"\nText length: {len(parsed.text)}")
    print(f"Code blocks: {len(parsed.code_blocks)}")
    print(f"Code results: {len(parsed.code_results)}")
    print(f"Images: {len(parsed.images)}")

    # Save images to disk for inspection
    output_dir = Path(r"D:\Projects\palimpsest\projects\vatican_alchemy\exports\debug_crops")
    output_dir.mkdir(parents=True, exist_ok=True)

    for i, img in enumerate(parsed.images):
        ext = img['mime_type'].split('/')[-1]
        img_path = output_dir / f"crop_{i}.{ext}"
        img_path.write_bytes(img['data'])
        print(f"  Saved: {img_path} ({len(img['data'])} bytes)")

    print(f"\n--- SUMMARY ---")
    print(f"response.text works correctly and returns the text content.")
    print(f"The SDK warning about non-text parts is informational, not an error.")


if __name__ == "__main__":
    debug_response_structure()
