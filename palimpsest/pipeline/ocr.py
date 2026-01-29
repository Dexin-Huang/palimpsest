"""Gemini OCR pipeline for manuscript processing.

This module provides two main capabilities:
1. GeminiOCR - Structured OCR that outputs Page JSON with zones and claims
2. transcribe_image() - Simple transcription that outputs text (for human review)

Model: gemini-3-flash-preview with Agentic Vision (code_execution tool)

Key configuration:
    - Use code_execution tool to enable Agentic Vision (model can zoom/crop)
    - Do NOT limit max_output_tokens - let the model complete fully
    - response.text correctly concatenates all text parts from multi-part response
    - Write to files, not stdout, on Windows to avoid Unicode encoding issues
"""

import json
import re
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from google import genai
from google.genai import types

from palimpsest.models import Confidence, Page, TextLayers, Zone, ZoneStyle
from palimpsest.models.page import (
    Claim,
    ImageInfo,
    Layout,
    Margins,
    PipelineInfo,
    SourceInfo,
    Span,
)


# Default model with Agentic Vision
DEFAULT_MODEL = "gemini-3-flash-preview"

# Prompt directory
PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def load_prompt(
    prompt_type: str,
    manuscript_name: str = "",
    manuscript_id: str = "",
    page_id: str = "",
) -> str:
    """Load a prompt template and substitute placeholders.

    Args:
        prompt_type: Name of prompt file (without .txt extension)
        manuscript_name: Human-readable manuscript name
        manuscript_id: Machine-readable manuscript identifier
        page_id: Page/folio identifier

    Returns:
        Prompt text with placeholders replaced

    Raises:
        FileNotFoundError: If prompt file does not exist
    """
    prompt_path = PROMPTS_DIR / f"{prompt_type}.txt"
    if not prompt_path.exists():
        available = [p.stem for p in PROMPTS_DIR.glob("*.txt")]
        raise FileNotFoundError(
            f"Prompt template not found: {prompt_path}\n"
            f"Available: {', '.join(available)}"
        )

    prompt = prompt_path.read_text(encoding="utf-8")

    # Substitute placeholders
    prompt = prompt.replace("{MANUSCRIPT_NAME}", manuscript_name)
    prompt = prompt.replace("{MANUSCRIPT_ID}", manuscript_id)
    prompt = prompt.replace("{PAGE_ID}", page_id)

    return prompt


def get_mime_type(path: Path) -> str:
    """Determine MIME type from file extension."""
    suffix = path.suffix.lower()
    mime_types = {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
        ".webp": "image/webp",
    }
    return mime_types.get(suffix, "image/jpeg")


@dataclass
class AgenticVisionResponse:
    """Parsed response from Gemini with Agentic Vision (code_execution enabled).

    When code_execution is enabled, Gemini returns a multi-part response:
    - executable_code: Python code the model executed to crop/zoom
    - code_execution_result: Output from running the code
    - inline_data: Images (cropped regions) the model created
    - text: The actual analysis/transcription text

    The response.text property returns only the text parts concatenated,
    which is usually what you want for transcription.
    """

    text: str
    code_blocks: List[dict]  # [{'language': str, 'code': str}]
    code_results: List[dict]  # [{'outcome': str, 'output': str}]
    images: List[dict]  # [{'mime_type': str, 'data': bytes}]
    raw_response: object


def extract_agentic_vision_response(response) -> AgenticVisionResponse:
    """Extract all content from a Gemini response with code_execution enabled.

    This is useful when you need access to the code blocks or cropped images
    that the model generated during analysis. For simple transcription,
    just use response.text instead.

    Args:
        response: The raw response from client.models.generate_content()

    Returns:
        AgenticVisionResponse with all extracted parts
    """
    text_parts = []
    code_blocks = []
    code_results = []
    images = []

    if hasattr(response, "candidates"):
        for candidate in response.candidates:
            if hasattr(candidate, "content") and hasattr(candidate.content, "parts"):
                for part in candidate.content.parts:
                    # Text parts
                    if hasattr(part, "text") and part.text:
                        text_parts.append(part.text)

                    # Code blocks
                    if hasattr(part, "executable_code") and part.executable_code:
                        code = part.executable_code
                        code_blocks.append(
                            {
                                "language": str(getattr(code, "language", "unknown")),
                                "code": getattr(code, "code", ""),
                            }
                        )

                    # Code execution results
                    if (
                        hasattr(part, "code_execution_result")
                        and part.code_execution_result
                    ):
                        result = part.code_execution_result
                        code_results.append(
                            {
                                "outcome": str(getattr(result, "outcome", "unknown")),
                                "output": getattr(result, "output", ""),
                            }
                        )

                    # Inline images (cropped regions)
                    if hasattr(part, "inline_data") and part.inline_data:
                        data = part.inline_data
                        images.append(
                            {
                                "mime_type": getattr(data, "mime_type", "unknown"),
                                "data": getattr(data, "data", b""),
                            }
                        )

    return AgenticVisionResponse(
        text="\n\n".join(text_parts),
        code_blocks=code_blocks,
        code_results=code_results,
        images=images,
        raw_response=response,
    )


def transcribe_image(
    image_path: Path,
    prompt_type: str = "transcription_enhanced",
    model: str = DEFAULT_MODEL,
    temperature: float = 0.1,
) -> str:
    """Transcribe a manuscript image using Gemini with Agentic Vision.

    This is the simple interface for getting a text transcription.
    For structured JSON output with zones/claims, use GeminiOCR.process_page().

    Args:
        image_path: Path to the image file
        prompt_type: Name of prompt template (default: transcription_enhanced)
        model: Gemini model name (default: gemini-3-flash-preview)
        temperature: Model temperature (default: 0.1 for consistency)

    Returns:
        Transcription text from the model
    """
    client = genai.Client()
    prompt = load_prompt(prompt_type)

    # Read and prepare image
    image_bytes = image_path.read_bytes()
    mime_type = get_mime_type(image_path)
    image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)

    # Call Gemini with Agentic Vision (code_execution enables zoom/crop)
    # Note: Do NOT set max_output_tokens - let the model complete fully
    response = client.models.generate_content(
        model=model,
        contents=[prompt, image_part],
        config=types.GenerateContentConfig(
            temperature=temperature,
            tools=[types.Tool(code_execution=types.ToolCodeExecution())],
        ),
    )

    # response.text correctly concatenates all text parts
    return response.text


class GeminiOCR:
    """Wrapper for Gemini vision API for structured manuscript OCR.

    This class produces structured Page JSON with zones, claims, and metadata.
    For simple text transcription, use the transcribe_image() function instead.
    """

    def __init__(self, model: str = DEFAULT_MODEL):
        """Initialize the OCR pipeline.

        Args:
            model: Gemini model to use. Default is gemini-3-flash-preview.
                   This model has Agentic Vision - can zoom/crop to read fine details.
                   Note: gemini-2.0-flash is NOT capable of reading medieval scripts.
        """
        self.client = genai.Client()
        self.model = model

    def process_page(
        self,
        image_path: Path,
        doc_id: str,
        page_id: str,
        prompt_type: str = "alchemical_text",
        source_info: Optional[dict] = None,
        manuscript_name: Optional[str] = None,
    ) -> Page:
        """Process a single manuscript page image into structured Page JSON.

        Args:
            image_path: Path to the page image file
            doc_id: Document identifier
            page_id: Page identifier (e.g., "f001r" for folio 1 recto)
            prompt_type: Which prompt template to use
            source_info: Optional source metadata dict
            manuscript_name: Human-readable name for prompt context

        Returns:
            Validated Page model
        """
        prompt = load_prompt(
            prompt_type,
            manuscript_name=manuscript_name or doc_id,
            manuscript_id=doc_id,
            page_id=page_id,
        )

        # Read image
        image_bytes = image_path.read_bytes()
        mime_type = get_mime_type(image_path)
        image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)

        # Call Gemini with Agentic Vision (code execution for zoom/crop)
        # Note: response_mime_type forces JSON output but may limit zoom capability
        response = self.client.models.generate_content(
            model=self.model,
            contents=[prompt, image_part],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1,
                tools=[types.Tool(code_execution=types.ToolCodeExecution())],
            ),
        )

        # Parse response
        try:
            result = json.loads(response.text)
        except json.JSONDecodeError as e:
            raise ValueError(
                f"Gemini returned invalid JSON: {e}\n{response.text[:500]}"
            )

        # Convert to Page model
        page = self._build_page(
            result=result,
            doc_id=doc_id,
            page_id=page_id,
            image_path=image_path,
            source_info=source_info,
        )

        return page

    def transcribe(
        self,
        image_path: Path,
        prompt_type: str = "transcription_enhanced",
    ) -> str:
        """Simple transcription without structured output.

        For human review of transcription quality. Returns plain text.

        Args:
            image_path: Path to the image file
            prompt_type: Prompt template name (default: transcription_enhanced)

        Returns:
            Transcription text
        """
        prompt = load_prompt(prompt_type)

        image_bytes = image_path.read_bytes()
        mime_type = get_mime_type(image_path)
        image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)

        # No response_mime_type - allow natural text output with Agentic Vision
        response = self.client.models.generate_content(
            model=self.model,
            contents=[prompt, image_part],
            config=types.GenerateContentConfig(
                temperature=0.1,
                tools=[types.Tool(code_execution=types.ToolCodeExecution())],
            ),
        )

        return response.text

    def process_batch(
        self,
        image_dir: Path,
        doc_id: str,
        prompt_type: str = "alchemical_text",
        parallel: int = 4,
        source_info: Optional[dict] = None,
        manuscript_name: Optional[str] = None,
    ) -> List[Page]:
        """Process a directory of page images in parallel.

        Args:
            image_dir: Directory containing page images
            doc_id: Document identifier
            prompt_type: Which prompt template to use
            parallel: Number of parallel workers
            source_info: Optional source metadata
            manuscript_name: Human-readable name for prompt context

        Returns:
            List of validated Page models
        """
        # Find all images
        image_paths = sorted(
            p
            for p in image_dir.iterdir()
            if p.suffix.lower() in (".jpg", ".jpeg", ".png", ".tif", ".tiff")
        )

        if not image_paths:
            raise ValueError(f"No images found in {image_dir}")

        pages = []
        errors = []

        with ThreadPoolExecutor(max_workers=parallel) as executor:
            futures = {}
            for i, img_path in enumerate(image_paths):
                page_id = self._derive_page_id(img_path, i)
                future = executor.submit(
                    self.process_page,
                    image_path=img_path,
                    doc_id=doc_id,
                    page_id=page_id,
                    prompt_type=prompt_type,
                    source_info=source_info,
                    manuscript_name=manuscript_name,
                )
                futures[future] = (img_path, page_id)

            for future in as_completed(futures):
                img_path, page_id = futures[future]
                try:
                    page = future.result()
                    pages.append(page)
                    print(f"[OK] {page_id}")
                except Exception as e:
                    errors.append((page_id, str(e)))
                    print(f"[ERROR] {page_id}: {e}")

        if errors:
            print(f"\n{len(errors)} pages failed:")
            for page_id, err in errors:
                print(f"  - {page_id}: {err}")

        return sorted(pages, key=lambda p: p.page_id)

    def _derive_page_id(self, path: Path, index: int) -> str:
        """Derive page ID from filename."""
        stem = path.stem.lower()

        # Match folio pattern (f001r, f001v, etc.)
        match = re.search(r"f(\d+)([rv])?", stem)
        if match:
            num = int(match.group(1))
            side = match.group(2) or "r"
            return f"f{num:03d}{side}"

        # Match page pattern (page_0001, p001, etc.)
        match = re.search(r"(?:page_?)?(\d+)", stem)
        if match:
            num = int(match.group(1))
            return f"p{num:04d}"

        # Fallback: use index
        return f"p{index + 1:04d}"

    def _build_page(
        self,
        result: dict,
        doc_id: str,
        page_id: str,
        image_path: Path,
        source_info: Optional[dict],
    ) -> Page:
        """Build a Page model from Gemini's JSON response."""
        width_px, height_px = self._get_image_dimensions(image_path)

        # Build layout
        layout_data = result.get("layout", {})
        layout = Layout(
            columns=layout_data.get("columns", 1),
            column_gap_norm=layout_data.get("column_gap_norm", 0.08),
            margins=(
                Margins(**layout_data["margins"])
                if "margins" in layout_data
                else None
            ),
            ruling=layout_data.get("ruling"),
        )

        # Build zones
        zones = []
        for z in result.get("zones", []):
            text_data = z.get("text", {})
            text = TextLayers(
                la_diplomatic=text_data.get("la_diplomatic"),
                la_normalized=text_data.get("la_normalized"),
                en_literal=text_data.get("en_literal"),
                en_interpreted=text_data.get("en_interpreted"),
            )

            style_data = z.get("style")
            style = ZoneStyle(**style_data) if style_data else None

            zone = Zone(
                zone_id=z["zone_id"],
                type=z["type"],
                order=z["order"],
                bbox_norm=tuple(z["bbox_norm"]),
                text=text,
                style=style,
            )
            zones.append(zone)

        # Build claims
        claims = []
        for c in result.get("claims", []):
            span_data = c.get("span")
            span = Span(**span_data) if span_data else None
            claim = Claim(
                claim_id=c["claim_id"],
                type=c["type"],
                value=c["value"],
                span=span,
                confidence=c.get("confidence"),
                attributes=c.get("attributes"),
            )
            claims.append(claim)

        # Build source info
        source = SourceInfo(**source_info) if source_info else None

        return Page(
            schema_version="rescript.page.v1",
            created_at=datetime.utcnow().isoformat() + "Z",
            page_id=page_id,
            doc_id=doc_id,
            source=source,
            image=ImageInfo(
                path=str(image_path),
                width_px=width_px,
                height_px=height_px,
            ),
            layout=layout,
            zones=zones,
            claims=claims if claims else None,
            pipeline=PipelineInfo(
                assumed_components={
                    "layout": f"gemini:{self.model}",
                    "transcription": f"gemini:{self.model}",
                    "normalization": f"gemini:{self.model}",
                    "translation": f"gemini:{self.model}",
                    "extraction": f"gemini:{self.model}",
                },
                model_versions={"ocr": self.model},
            ),
        )

    def _get_image_dimensions(self, path: Path) -> tuple[int, int]:
        """Get image dimensions without loading full image."""
        try:
            from PIL import Image

            with Image.open(path) as img:
                return img.size
        except ImportError:
            return self._read_jpeg_dimensions(path)

    def _read_jpeg_dimensions(self, path: Path) -> tuple[int, int]:
        """Read JPEG dimensions from file header."""
        with open(path, "rb") as f:
            f.seek(0)
            if f.read(2) != b"\xff\xd8":
                return (1000, 1400)  # Default fallback

            while True:
                marker = f.read(2)
                if len(marker) < 2:
                    break
                if marker[0] != 0xFF:
                    break
                if marker[1] == 0xC0 or marker[1] == 0xC2:  # SOF0 or SOF2
                    f.read(3)  # Skip length and precision
                    height = int.from_bytes(f.read(2), "big")
                    width = int.from_bytes(f.read(2), "big")
                    return (width, height)
                else:
                    length = int.from_bytes(f.read(2), "big")
                    f.seek(length - 2, 1)

        return (1000, 1400)  # Default fallback
