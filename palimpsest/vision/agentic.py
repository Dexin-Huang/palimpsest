from __future__ import annotations

from dataclasses import dataclass
from typing import List


@dataclass
class AgenticVisionResponse:
    """Parsed response from Gemini with code_execution enabled."""

    text: str
    code_blocks: List[dict]
    code_results: List[dict]
    images: List[dict]
    raw_response: object


def extract_agentic_vision_response(response) -> AgenticVisionResponse:
    """Extract text, code, results, and images from a Gemini response."""
    text_parts = []
    code_blocks = []
    code_results = []
    images = []

    if hasattr(response, "candidates"):
        for candidate in response.candidates:
            if hasattr(candidate, "content") and hasattr(candidate.content, "parts"):
                for part in candidate.content.parts:
                    if hasattr(part, "text") and part.text:
                        text_parts.append(part.text)
                    if hasattr(part, "executable_code") and part.executable_code:
                        code = part.executable_code
                        code_blocks.append({
                            "language": str(getattr(code, "language", "unknown")),
                            "code": getattr(code, "code", ""),
                        })
                    if hasattr(part, "code_execution_result") and part.code_execution_result:
                        result = part.code_execution_result
                        code_results.append({
                            "outcome": str(getattr(result, "outcome", "unknown")),
                            "output": getattr(result, "output", ""),
                        })
                    if hasattr(part, "inline_data") and part.inline_data:
                        data = part.inline_data
                        images.append({
                            "mime_type": getattr(data, "mime_type", "unknown"),
                            "data": getattr(data, "data", b""),
                        })

    return AgenticVisionResponse(
        text="\n\n".join(text_parts),
        code_blocks=code_blocks,
        code_results=code_results,
        images=images,
        raw_response=response,
    )

