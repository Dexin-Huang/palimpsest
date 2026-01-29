"""Multi-pass vision pipeline using Gemini with Agentic Vision.

This module orchestrates multi-pass analysis of manuscript pages:
1. Segmentation - Identify regions (text, illustration, marginalia, etc.)
2. Transcription - Diplomatic and normalized text per region
3. Translation - English layers (literal + interpreted)
4. Claims - Extract structured data (names, dates, places)

Uses Gemini 3 Flash with code_execution for Agentic Vision capabilities,
allowing the model to zoom/crop to read fine details.
"""

import json
import time
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple

from google import genai
from google.genai import types

from palimpsest.models import Page, Zone, TextLayers, ZoneStyle, Confidence
from palimpsest.models.page import (
    Claim, ImageInfo, Layout, Margins, PipelineInfo, SourceInfo, Span
)
from .segmentation import (
    SegmentationResult, SegmentedRegion, RegionType,
    LayoutAnalysis, IllustrationAnalysis
)
from .ocr import extract_agentic_vision_response


# Default models
DEFAULT_VISION_MODEL = "gemini-3-flash-preview"

# Prompt directory
PROMPTS_DIR = Path(__file__).parent.parent / "prompts"


def _load_prompt(prompt_name: str, **kwargs) -> str:
    """Load and format a prompt template."""
    path = PROMPTS_DIR / f"{prompt_name}.txt"
    if not path.exists():
        raise FileNotFoundError(f"Prompt not found: {path}")

    template = path.read_text(encoding="utf-8")
    for key, value in kwargs.items():
        template = template.replace(f"{{{key.upper()}}}", str(value))
    return template


def _get_mime_type(path: Path) -> str:
    """Get MIME type from file extension."""
    suffix = path.suffix.lower()
    return {
        ".jpg": "image/jpeg",
        ".jpeg": "image/jpeg",
        ".png": "image/png",
        ".tif": "image/tiff",
        ".tiff": "image/tiff",
        ".webp": "image/webp",
    }.get(suffix, "image/jpeg")


def _get_image_dimensions(path: Path) -> Tuple[int, int]:
    """Get image dimensions."""
    try:
        from PIL import Image
        with Image.open(path) as img:
            return img.size
    except ImportError:
        # Fallback for JPEG without PIL
        return _read_jpeg_dimensions(path)


def _read_jpeg_dimensions(path: Path) -> Tuple[int, int]:
    """Read JPEG dimensions from header."""
    with open(path, "rb") as f:
        if f.read(2) != b"\xff\xd8":
            return (1000, 1400)

        while True:
            marker = f.read(2)
            if len(marker) < 2 or marker[0] != 0xFF:
                break
            if marker[1] in (0xC0, 0xC2):
                f.read(3)
                height = int.from_bytes(f.read(2), "big")
                width = int.from_bytes(f.read(2), "big")
                return (width, height)
            else:
                length = int.from_bytes(f.read(2), "big")
                f.seek(length - 2, 1)

    return (1000, 1400)


@dataclass
class PipelineConfig:
    """Configuration for VisionPipeline."""

    model: str = DEFAULT_VISION_MODEL
    temperature: float = 0.1

    # Which passes to run
    passes: List[str] = None  # ["segmentation", "transcription", "translation", "claims"]

    # Segmentation options
    segment_illustrations: bool = True

    # Transcription options
    transcribe_marginalia: bool = True
    include_diplomatic: bool = True
    include_normalized: bool = True

    # Translation options
    include_literal: bool = True
    include_interpreted: bool = True
    target_language: str = "en"

    # Claims options
    extract_persons: bool = True
    extract_places: bool = True
    extract_dates: bool = True
    extract_substances: bool = True  # For alchemical texts

    # Debugging / tracing
    save_agentic_trace: bool = False
    trace_dir: Optional[str] = None

    def __post_init__(self):
        if self.passes is None:
            self.passes = ["segmentation", "transcription", "translation", "claims"]


class VisionPipeline:
    """Multi-pass vision analysis pipeline.

    Uses Gemini 3 Flash with Agentic Vision (code_execution) for
    comprehensive manuscript page analysis.

    Example:
        pipeline = VisionPipeline()
        page = pipeline.analyze_page(
            image_path=Path("f001r.jpg"),
            doc_id="pal_lat_1267",
            page_id="f001r",
        )
    """

    def __init__(
        self,
        model: str = DEFAULT_VISION_MODEL,
        config: Optional[PipelineConfig] = None,
    ):
        """Initialize the pipeline.

        Args:
            model: Gemini model to use
            config: Pipeline configuration
        """
        self.client = genai.Client()
        self.model = model
        self.config = config or PipelineConfig(model=model)

    def _resolve_trace_dir(self, image_path: Optional[Path]) -> Path:
        """Resolve output directory for agentic traces."""
        if self.config.trace_dir:
            return Path(self.config.trace_dir)
        if image_path:
            try:
                # projects/<proj>/images/<file> -> projects/<proj>/exports/agentic
                project_dir = image_path.parents[1]
                return project_dir / "exports" / "agentic"
            except Exception:
                pass
        return Path("exports") / "agentic"

    def _save_agentic_trace(
        self,
        page_id: str,
        pass_name: str,
        response,
        image_path: Optional[Path],
    ) -> None:
        """Persist code execution traces and inline images."""
        if not self.config.save_agentic_trace:
            return

        trace_dir = self._resolve_trace_dir(image_path)
        trace_dir.mkdir(parents=True, exist_ok=True)

        parsed = extract_agentic_vision_response(response)

        images_info = []
        for idx, img in enumerate(parsed.images):
            mime = img.get("mime_type", "image/png")
            ext = {
                "image/jpeg": ".jpg",
                "image/jpg": ".jpg",
                "image/png": ".png",
                "image/webp": ".webp",
                "image/tiff": ".tif",
            }.get(mime, ".bin")
            fname = f"{page_id}_{pass_name}_{idx}{ext}"
            (trace_dir / fname).write_bytes(img.get("data", b""))
            images_info.append({"mime_type": mime, "file": fname})

        usage = None
        if hasattr(response, "usage_metadata") and response.usage_metadata:
            usage = {
                "prompt_token_count": getattr(response.usage_metadata, "prompt_token_count", None),
                "candidates_token_count": getattr(response.usage_metadata, "candidates_token_count", None),
                "total_token_count": getattr(response.usage_metadata, "total_token_count", None),
                "thoughts_token_count": getattr(response.usage_metadata, "thoughts_token_count", None),
            }

        payload = {
            "page_id": page_id,
            "pass": pass_name,
            "model": self.model,
            "saved_at": datetime.utcnow().isoformat() + "Z",
            "text": parsed.text,
            "code_blocks": parsed.code_blocks,
            "code_results": parsed.code_results,
            "images": images_info,
            "usage": usage,
        }

        out_path = trace_dir / f"{page_id}_{pass_name}.json"
        out_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def analyze_page(
        self,
        image_path: Path,
        doc_id: str,
        page_id: str,
        passes: Optional[List[str]] = None,
        source_info: Optional[Dict[str, Any]] = None,
        manuscript_name: Optional[str] = None,
    ) -> Page:
        """Run multi-pass analysis on a page image.

        Args:
            image_path: Path to page image
            doc_id: Document identifier
            page_id: Page identifier (e.g., "f001r")
            passes: Which passes to run (default: all)
            source_info: Optional source metadata
            manuscript_name: Human-readable manuscript name

        Returns:
            Fully analyzed Page model
        """
        passes = passes or self.config.passes
        start_time = time.time()

        # Get image info
        width, height = _get_image_dimensions(image_path)
        image_bytes = image_path.read_bytes()
        mime_type = _get_mime_type(image_path)

        # Initialize result containers
        segmentation: Optional[SegmentationResult] = None
        zones: List[Zone] = []
        claims: List[Claim] = []
        passes_completed: List[str] = []

        # Pass 1: Segmentation
        if "segmentation" in passes:
            segmentation = self._run_segmentation(
                image_bytes, mime_type, page_id, width, height, image_path
            )
            passes_completed.append("segmentation")

        # Pass 2: Transcription
        if "transcription" in passes:
            zones = self._run_transcription(
                image_bytes, mime_type, segmentation, page_id, image_path
            )
            passes_completed.append("transcription")

        # Pass 3: Translation
        if "translation" in passes and zones:
            zones = self._run_translation(
                image_bytes, mime_type, zones, page_id, image_path
            )
            passes_completed.append("translation")

        # Pass 4: Claims extraction
        if "claims" in passes and zones:
            claims = self._run_claims_extraction(
                image_bytes, mime_type, zones, page_id, image_path
            )
            passes_completed.append("claims")

        # Build final Page model
        elapsed_ms = int((time.time() - start_time) * 1000)

        # Convert segmentation layout to Layout model
        layout = None
        if segmentation:
            layout = Layout(
                columns=segmentation.layout.columns,
                column_gap_norm=segmentation.layout.column_gap,
                margins=Margins(
                    top=segmentation.layout.margin_top,
                    bottom=segmentation.layout.margin_bottom,
                    inner=segmentation.layout.margin_inner,
                    outer=segmentation.layout.margin_outer,
                ) if any([
                    segmentation.layout.margin_top,
                    segmentation.layout.margin_bottom,
                ]) else None,
                ruling=segmentation.layout.ruling_type,
            )

        return Page(
            schema_version="rescript.page.v1",
            created_at=datetime.utcnow().isoformat() + "Z",
            page_id=page_id,
            doc_id=doc_id,
            source=SourceInfo(**source_info) if source_info else None,
            image=ImageInfo(
                path=str(image_path),
                width_px=width,
                height_px=height,
            ),
            layout=layout,
            zones=zones if zones else self._create_fallback_zones(segmentation),
            claims=claims if claims else None,
            pipeline=PipelineInfo(
                assumed_components={
                    p: f"gemini:{self.model}" for p in passes_completed
                },
                model_versions={
                    "vision_pipeline": self.model,
                },
                notes=f"Processed in {elapsed_ms}ms with passes: {', '.join(passes_completed)}",
            ),
        )

    def segment_page(
        self,
        image_path: Path,
        page_id: str,
    ) -> SegmentationResult:
        """Run only the segmentation pass.

        Args:
            image_path: Path to page image
            page_id: Page identifier

        Returns:
            SegmentationResult with detected regions
        """
        width, height = _get_image_dimensions(image_path)
        image_bytes = image_path.read_bytes()
        mime_type = _get_mime_type(image_path)

        return self._run_segmentation(
            image_bytes, mime_type, page_id, width, height
        )

    def analyze_illustration(
        self,
        image_path: Path,
        bbox_norm: Tuple[float, float, float, float],
        context: Optional[str] = None,
    ) -> IllustrationAnalysis:
        """Deep analysis of an illustration region.

        Args:
            image_path: Path to full page image
            bbox_norm: Normalized bounding box [x, y, w, h]
            context: Optional context about the manuscript

        Returns:
            Detailed IllustrationAnalysis
        """
        image_bytes = image_path.read_bytes()
        mime_type = _get_mime_type(image_path)

        prompt = _load_prompt(
            "illustration_analysis",
            bbox=str(list(bbox_norm)),
            context=context or "Medieval manuscript illustration",
        )

        image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)

        response = self.client.models.generate_content(
            model=self.model,
            contents=[prompt, image_part],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=self.config.temperature,
                tools=[types.Tool(code_execution=types.ToolCodeExecution())],
            ),
        )

        try:
            result = json.loads(response.text)
        except json.JSONDecodeError:
            return IllustrationAnalysis(
                region_id="ill_0",
                bbox_norm=bbox_norm,
                subject="Analysis failed - could not parse response",
            )

        return IllustrationAnalysis(
            region_id=result.get("region_id", "ill_0"),
            bbox_norm=bbox_norm,
            subject=result.get("subject", ""),
            figures=result.get("figures", []),
            objects=result.get("objects", []),
            symbols=result.get("symbols", []),
            labels=result.get("labels", []),
            inscriptions=result.get("inscriptions", []),
            technique=result.get("technique"),
            colors=result.get("colors", []),
            style_period=result.get("style_period"),
            iconographic_type=result.get("iconographic_type"),
            related_text=result.get("related_text"),
            condition=result.get("condition", "good"),
            condition_notes=result.get("condition_notes"),
        )

    def _run_segmentation(
        self,
        image_bytes: bytes,
        mime_type: str,
        page_id: str,
        width: int,
        height: int,
        image_path: Optional[Path] = None,
    ) -> SegmentationResult:
        """Run segmentation pass."""
        prompt = _load_prompt("segmentation", page_id=page_id)
        image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)

        response = self.client.models.generate_content(
            model=self.model,
            contents=[prompt, image_part],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=self.config.temperature,
                tools=[types.Tool(code_execution=types.ToolCodeExecution())],
            ),
        )
        self._save_agentic_trace(page_id, "segmentation", response, image_path)

        try:
            result = json.loads(response.text)
        except json.JSONDecodeError:
            # Return empty segmentation on failure
            return SegmentationResult(
                page_id=page_id,
                image_width=width,
                image_height=height,
                model=self.model,
            )

        # Parse layout
        layout_data = result.get("layout", {})
        ruling_type = layout_data.get("ruling_type")
        if isinstance(ruling_type, str):
            ruling_type = ruling_type.strip().lower().replace(" ", "_")
            if ruling_type not in {"none", "dry_point", "lead", "ink"}:
                ruling_type = None
        layout = LayoutAnalysis(
            columns=layout_data.get("columns", 1),
            column_widths=layout_data.get("column_widths", []),
            column_gap=layout_data.get("column_gap", 0.0),
            margin_top=layout_data.get("margin_top", 0.05),
            margin_bottom=layout_data.get("margin_bottom", 0.05),
            margin_inner=layout_data.get("margin_inner", 0.08),
            margin_outer=layout_data.get("margin_outer", 0.12),
            has_ruling=layout_data.get("has_ruling", False),
            ruling_type=ruling_type,
            page_type=layout_data.get("page_type", "text"),
        )

        # Parse regions
        regions = []
        for r in result.get("regions", []):
            try:
                region = SegmentedRegion(
                    region_id=r["region_id"],
                    region_type=RegionType(r["region_type"]),
                    bbox_norm=tuple(r["bbox_norm"]),
                    confidence=r.get("confidence", 1.0),
                    reading_order=r.get("reading_order"),
                    column=r.get("column"),
                    estimated_lines=r.get("estimated_lines"),
                    has_decoration=r.get("has_decoration", False),
                    ink_colors=r.get("ink_colors", []),
                    script_style=r.get("script_style"),
                    subject_hint=r.get("subject_hint"),
                    contains_text=r.get("contains_text", False),
                )
                regions.append(region)
            except (KeyError, ValueError):
                continue

        return SegmentationResult(
            page_id=page_id,
            image_width=width,
            image_height=height,
            layout=layout,
            regions=regions,
            model=self.model,
            passes_completed=["segmentation"],
        )

    def _run_transcription(
        self,
        image_bytes: bytes,
        mime_type: str,
        segmentation: Optional[SegmentationResult],
        page_id: str,
        image_path: Optional[Path] = None,
    ) -> List[Zone]:
        """Run transcription pass."""
        # Build prompt with region hints if available
        region_hints = ""
        if segmentation:
            text_regions = segmentation.get_text_regions()
            if text_regions:
                hints = []
                for r in text_regions:
                    hints.append(f"- {r.region_id}: {r.region_type.value} at {r.bbox_norm}")
                region_hints = "Known regions:\n" + "\n".join(hints)

        prompt = _load_prompt(
            "transcription_only",
            page_id=page_id,
            region_hints=region_hints,
        )

        image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)

        response = self.client.models.generate_content(
            model=self.model,
            contents=[prompt, image_part],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=self.config.temperature,
                tools=[types.Tool(code_execution=types.ToolCodeExecution())],
            ),
        )
        self._save_agentic_trace(page_id, "transcription", response, image_path)

        try:
            result = json.loads(response.text)
        except json.JSONDecodeError:
            return []

        zones = []
        for z in result.get("zones", []):
            text_data = z.get("text", {})
            zones.append(Zone(
                zone_id=z.get("zone_id", f"z{len(zones)}"),
                type=z.get("type", "line"),
                order=z.get("order", len(zones)),
                bbox_norm=tuple(z.get("bbox_norm", [0, 0, 1, 0.05])),
                text=TextLayers(
                    la_diplomatic=text_data.get("la_diplomatic"),
                    la_normalized=text_data.get("la_normalized"),
                    es_diplomatic=text_data.get("es_diplomatic"),
                    es_normalized=text_data.get("es_normalized"),
                ),
                style=ZoneStyle(
                    color=z.get("style", {}).get("color"),
                    font_size=z.get("style", {}).get("font_size"),
                ) if z.get("style") else None,
                confidence=Confidence(
                    transcription=z.get("confidence", {}).get("transcription"),
                ) if z.get("confidence") else None,
            ))

        return zones

    def _run_translation(
        self,
        image_bytes: bytes,
        mime_type: str,
        zones: List[Zone],
        page_id: str,
        image_path: Optional[Path] = None,
    ) -> List[Zone]:
        """Run translation pass to add English layers."""
        # Build text for translation
        texts_to_translate = []
        for z in zones:
            source_text = z.text.la_normalized or z.text.la_diplomatic or \
                         z.text.es_normalized or z.text.es_diplomatic
            if source_text:
                texts_to_translate.append({
                    "zone_id": z.zone_id,
                    "text": source_text,
                    "type": z.type,
                })

        if not texts_to_translate:
            return zones

        prompt = f"""Translate the following manuscript text zones to English.
Provide both literal and interpreted translations.

Page: {page_id}

Zones to translate:
{json.dumps(texts_to_translate, indent=2)}

Return JSON with format:
{{
  "translations": [
    {{
      "zone_id": "z1",
      "en_literal": "...",
      "en_interpreted": "..."
    }}
  ]
}}
"""

        image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)

        response = self.client.models.generate_content(
            model=self.model,
            contents=[prompt, image_part],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=self.config.temperature,
            ),
        )
        self._save_agentic_trace(page_id, "translation", response, image_path)

        try:
            result = json.loads(response.text)
        except json.JSONDecodeError:
            return zones

        # Apply translations to zones
        translations = {t["zone_id"]: t for t in result.get("translations", [])}
        for zone in zones:
            if zone.zone_id in translations:
                t = translations[zone.zone_id]
                zone.text.en_literal = t.get("en_literal")
                zone.text.en_interpreted = t.get("en_interpreted")

        return zones

    def _run_claims_extraction(
        self,
        image_bytes: bytes,
        mime_type: str,
        zones: List[Zone],
        page_id: str,
        image_path: Optional[Path] = None,
    ) -> List[Claim]:
        """Run claims extraction pass."""
        # Collect all text for analysis
        all_text = []
        for z in zones:
            text = z.text.en_interpreted or z.text.en_literal or \
                   z.text.la_normalized or z.text.la_diplomatic
            if text:
                all_text.append(f"[{z.zone_id}] {text}")

        if not all_text:
            return []

        claim_types = []
        if self.config.extract_persons:
            claim_types.append("person (names, roles, relationships)")
        if self.config.extract_places:
            claim_types.append("place (locations, geographic references)")
        if self.config.extract_dates:
            claim_types.append("date (temporal references)")
        if self.config.extract_substances:
            claim_types.append("substance (materials, chemicals, ingredients)")

        prompt = f"""Extract structured claims from this manuscript page.

Page: {page_id}

Text content:
{chr(10).join(all_text)}

Extract claims of types: {', '.join(claim_types)}

Return JSON with format:
{{
  "claims": [
    {{
      "claim_id": "c1",
      "type": "person",
      "value": "Aristotle",
      "span": {{"zone_id": "z1", "char_start": 0, "char_end": 9}},
      "confidence": 0.95,
      "attributes": {{"role": "philosopher"}}
    }}
  ]
}}
"""

        image_part = types.Part.from_bytes(data=image_bytes, mime_type=mime_type)

        response = self.client.models.generate_content(
            model=self.model,
            contents=[prompt, image_part],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=self.config.temperature,
            ),
        )
        self._save_agentic_trace(page_id, "claims", response, image_path)

        try:
            result = json.loads(response.text)
        except json.JSONDecodeError:
            return []

        claims = []
        for c in result.get("claims", []):
            span_data = c.get("span")
            claims.append(Claim(
                claim_id=c.get("claim_id", f"c{len(claims)}"),
                type=c.get("type", "unknown"),
                value=c.get("value", ""),
                span=Span(
                    zone_id=span_data["zone_id"],
                    char_start=span_data.get("char_start", 0),
                    char_end=span_data.get("char_end", 0),
                ) if span_data else None,
                confidence=c.get("confidence"),
                attributes=c.get("attributes"),
            ))

        return claims

    def _create_fallback_zones(
        self,
        segmentation: Optional[SegmentationResult],
    ) -> List[Zone]:
        """Create fallback zones from segmentation if transcription failed."""
        if not segmentation:
            # Return a single full-page zone
            return [Zone(
                zone_id="z0",
                type="unknown",
                order=0,
                bbox_norm=(0, 0, 1, 1),
                text=TextLayers(),
            )]

        zones = []
        for i, region in enumerate(segmentation.get_text_regions()):
            zone_type = {
                RegionType.TEXT_BLOCK: "line",
                RegionType.TEXT_LINE: "line",
                RegionType.RUBRIC: "rubric",
                RegionType.MARGINALIA: "marginalia",
                RegionType.INITIAL: "initial",
                RegionType.COLOPHON: "colophon",
                RegionType.CATCHWORD: "catchword",
            }.get(region.region_type, "unknown")

            zones.append(Zone(
                zone_id=region.region_id,
                type=zone_type,
                order=region.reading_order or i,
                bbox_norm=region.bbox_norm,
                text=TextLayers(),
            ))

        return zones if zones else [Zone(
            zone_id="z0",
            type="unknown",
            order=0,
            bbox_norm=(0, 0, 1, 1),
            text=TextLayers(),
        )]
