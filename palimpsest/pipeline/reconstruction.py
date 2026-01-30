"""Reconstruction pipeline for generating English scanlations.

Uses Gemini 3 Pro Image Preview (Nano Banana Pro) for image generation
to create beautiful English reconstructions of manuscript pages.

Reconstruction Modes:
- overlay: Transparent PNG with English text at zone positions
- clean: AI-generated clean page with English (removes original)
- hybrid: Original background + translated text overlay
- scholarly: Side-by-side original + annotated
"""

import base64
import json
from dataclasses import dataclass, field
from datetime import datetime
from io import BytesIO
from pathlib import Path
from typing import List, Optional, Dict, Any, Tuple, Literal

from google import genai
from google.genai import types

from palimpsest.models import Page, Zone
from palimpsest.config import DEFAULT_MODEL_RECON, DEFAULT_MODEL_VISION


# Models
RECONSTRUCTION_MODEL = DEFAULT_MODEL_RECON
VISION_MODEL = DEFAULT_MODEL_VISION

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


ReconstructionMode = Literal["overlay", "clean", "hybrid", "scholarly"]


@dataclass
class TextPosition:
    """Positioned text for overlay generation."""

    text: str
    x: float  # Normalized 0-1
    y: float
    width: float
    height: float
    font_size: str = "normal"  # small, normal, large, initial
    color: str = "black"
    style: str = "normal"  # normal, italic, bold


@dataclass
class ReconstructionResult:
    """Result of page reconstruction."""

    page_id: str
    mode: ReconstructionMode

    # Generated images (as bytes)
    original_image: Optional[bytes] = None
    overlay_image: Optional[bytes] = None  # Transparent PNG
    reconstructed_image: Optional[bytes] = None  # Full reconstruction
    comparison_image: Optional[bytes] = None  # Side-by-side

    # Text positions used for overlay
    text_positions: List[TextPosition] = field(default_factory=list)

    # Metadata
    model: str = ""
    generated_at: str = ""
    notes: Optional[str] = None

    def save_package(self, output_dir: Path) -> Dict[str, Path]:
        """Save reconstruction package to directory.

        Returns:
            Dict mapping image type to saved file path
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        saved = {}

        if self.original_image:
            path = output_dir / "original.jpg"
            path.write_bytes(self.original_image)
            saved["original"] = path

        if self.overlay_image:
            path = output_dir / "overlay.png"
            path.write_bytes(self.overlay_image)
            saved["overlay"] = path

        if self.reconstructed_image:
            path = output_dir / "reconstructed.png"
            path.write_bytes(self.reconstructed_image)
            saved["reconstructed"] = path

        if self.comparison_image:
            path = output_dir / "comparison.png"
            path.write_bytes(self.comparison_image)
            saved["comparison"] = path

        # Save metadata
        meta_path = output_dir / "reconstruction.json"
        meta_path.write_text(json.dumps({
            "page_id": self.page_id,
            "mode": self.mode,
            "model": self.model,
            "generated_at": self.generated_at,
            "text_positions": [
                {
                    "text": tp.text,
                    "x": tp.x, "y": tp.y,
                    "width": tp.width, "height": tp.height,
                    "font_size": tp.font_size,
                    "color": tp.color,
                    "style": tp.style,
                }
                for tp in self.text_positions
            ],
            "notes": self.notes,
        }, indent=2))
        saved["metadata"] = meta_path

        return saved


class ReconstructionPipeline:
    """Pipeline for generating English scanlation reconstructions.

    Uses Gemini 3 Pro Image Preview for AI image generation
    and standard image processing for overlays.

    Example:
        pipeline = ReconstructionPipeline()
        result = pipeline.reconstruct_page(
            original_image=Path("f001r.jpg"),
            page_json=page,
            mode="hybrid",
        )
        result.save_package(Path("reconstruction/f001r/"))
    """

    def __init__(
        self,
        model: str = RECONSTRUCTION_MODEL,
        vision_model: str = VISION_MODEL,
    ):
        """Initialize the reconstruction pipeline.

        Args:
            model: Model for image generation (Nano Banana Pro)
            vision_model: Model for text analysis (Gemini Flash)
        """
        self.client = genai.Client()
        self.model = model
        self.vision_model = vision_model

    def reconstruct_page(
        self,
        original_image: Path,
        page_json: Page,
        mode: ReconstructionMode = "hybrid",
        text_layer: str = "en_interpreted",
    ) -> ReconstructionResult:
        """Generate reconstructed scanlation of a page.

        Args:
            original_image: Path to original manuscript image
            page_json: Page model with zones and translations
            mode: Reconstruction mode (overlay, clean, hybrid, scholarly)
            text_layer: Which text layer to use for English

        Returns:
            ReconstructionResult with generated images
        """
        result = ReconstructionResult(
            page_id=page_json.page_id,
            mode=mode,
            model=self.model,
            generated_at=datetime.utcnow().isoformat() + "Z",
        )

        # Read original image
        result.original_image = original_image.read_bytes()

        # Extract text positions
        result.text_positions = self._extract_text_positions(
            page_json, text_layer
        )

        if mode == "overlay":
            result.overlay_image = self._generate_text_overlay(
                page_json, text_layer
            )

        elif mode == "clean":
            result.reconstructed_image = self._generate_clean_reconstruction(
                original_image, page_json, text_layer
            )

        elif mode == "hybrid":
            result.overlay_image = self._generate_text_overlay(
                page_json, text_layer
            )
            result.reconstructed_image = self._composite_hybrid(
                result.original_image,
                result.overlay_image,
            )

        elif mode == "scholarly":
            result.overlay_image = self._generate_scholarly_overlay(
                page_json, text_layer
            )
            result.comparison_image = self._generate_comparison(
                result.original_image,
                result.overlay_image,
                page_json,
            )

        return result

    def generate_text_overlay(
        self,
        page_json: Page,
        text_layer: str = "en_interpreted",
    ) -> bytes:
        """Generate transparent PNG with positioned text.

        Args:
            page_json: Page model with zones
            text_layer: Which text layer to render

        Returns:
            PNG image bytes with transparent background
        """
        return self._generate_text_overlay(page_json, text_layer)

    def generate_clean_reconstruction(
        self,
        original_image: Path,
        page_json: Page,
        text_layer: str = "en_interpreted",
    ) -> bytes:
        """Generate AI-reconstructed English version.

        Uses Nano Banana Pro to create a clean manuscript-style
        page with English text.

        Args:
            original_image: Path to original image
            page_json: Page model with translations
            text_layer: Which text layer to use

        Returns:
            Generated image bytes
        """
        return self._generate_clean_reconstruction(
            original_image, page_json, text_layer
        )

    def _extract_text_positions(
        self,
        page_json: Page,
        text_layer: str,
    ) -> List[TextPosition]:
        """Extract positioned text from page zones."""
        positions = []

        for zone in page_json.zones_by_order():
            text = zone.text.get_layer(text_layer)
            if not text:
                # Fallback to other English layers
                text = zone.text.en_interpreted or zone.text.en_literal
            if not text:
                continue

            # Determine font size based on zone type
            font_size = "normal"
            if zone.type == "initial":
                font_size = "initial"
            elif zone.type == "rubric":
                font_size = "large"
            elif zone.type in ("marginalia", "catchword"):
                font_size = "small"

            # Determine style
            style = "normal"
            if zone.type == "rubric":
                style = "bold"
            elif zone.type == "marginalia":
                style = "italic"

            # Determine color
            color = "black"
            if zone.style and zone.style.color == "rubric":
                color = "#8B0000"  # Dark red

            positions.append(TextPosition(
                text=text,
                x=zone.x,
                y=zone.y,
                width=zone.width,
                height=zone.height,
                font_size=font_size,
                color=color,
                style=style,
            ))

        return positions

    def _generate_text_overlay(
        self,
        page_json: Page,
        text_layer: str,
    ) -> bytes:
        """Generate transparent PNG overlay with positioned text.

        Uses PIL/Pillow for text rendering if available,
        otherwise generates via Gemini.
        """
        try:
            return self._generate_overlay_with_pillow(page_json, text_layer)
        except ImportError:
            return self._generate_overlay_with_gemini(page_json, text_layer)

    def _generate_overlay_with_pillow(
        self,
        page_json: Page,
        text_layer: str,
    ) -> bytes:
        """Generate overlay using Pillow."""
        from PIL import Image, ImageDraw, ImageFont

        # Create transparent image
        width = page_json.image.width_px
        height = page_json.image.height_px
        img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)

        # Try to load a nice serif font, fall back to default
        try:
            font_normal = ImageFont.truetype("times.ttf", 24)
            font_large = ImageFont.truetype("times.ttf", 32)
            font_small = ImageFont.truetype("times.ttf", 18)
            font_initial = ImageFont.truetype("times.ttf", 72)
        except OSError:
            font_normal = ImageFont.load_default()
            font_large = font_normal
            font_small = font_normal
            font_initial = font_normal

        fonts = {
            "normal": font_normal,
            "large": font_large,
            "small": font_small,
            "initial": font_initial,
        }

        # Render each text position
        for zone in page_json.zones_by_order():
            text = zone.text.get_layer(text_layer)
            if not text:
                text = zone.text.en_interpreted or zone.text.en_literal
            if not text:
                continue

            # Calculate pixel position
            x = int(zone.x * width)
            y = int(zone.y * height)
            box_width = int(zone.width * width)
            box_height = int(zone.height * height)

            # Select font
            font_size = "normal"
            if zone.type == "initial":
                font_size = "initial"
            elif zone.type == "rubric":
                font_size = "large"
            elif zone.type in ("marginalia", "catchword"):
                font_size = "small"
            font = fonts.get(font_size, font_normal)

            # Determine color
            if zone.style and zone.style.color == "rubric":
                color = (139, 0, 0, 255)  # Dark red
            else:
                color = (0, 0, 0, 255)  # Black

            # Word wrap text to fit box
            words = text.split()
            lines = []
            current_line = []

            for word in words:
                test_line = " ".join(current_line + [word])
                bbox = draw.textbbox((0, 0), test_line, font=font)
                if bbox[2] <= box_width:
                    current_line.append(word)
                else:
                    if current_line:
                        lines.append(" ".join(current_line))
                    current_line = [word]

            if current_line:
                lines.append(" ".join(current_line))

            # Draw lines
            line_height = font.size if hasattr(font, "size") else 24
            for i, line in enumerate(lines):
                line_y = y + i * int(line_height * 1.2)
                if line_y + line_height <= y + box_height:
                    draw.text((x, line_y), line, font=font, fill=color)

        # Save to bytes
        buffer = BytesIO()
        img.save(buffer, format="PNG")
        return buffer.getvalue()

    def _generate_overlay_with_gemini(
        self,
        page_json: Page,
        text_layer: str,
    ) -> bytes:
        """Generate overlay using Gemini image generation."""
        # Build text layout description
        text_items = []
        for zone in page_json.zones_by_order():
            text = zone.text.get_layer(text_layer)
            if not text:
                text = zone.text.en_interpreted or zone.text.en_literal
            if not text:
                continue

            text_items.append({
                "text": text,
                "x": zone.x,
                "y": zone.y,
                "width": zone.width,
                "height": zone.height,
                "type": zone.type,
            })

        prompt = f"""Generate a transparent PNG image with English text positioned as follows.
The image should be {page_json.image.width_px}x{page_json.image.height_px} pixels.
Use an elegant serif font (like Times or Garamond).
Background must be fully transparent.

Text positions (normalized 0-1 coordinates):
{json.dumps(text_items, indent=2)}

Position each text block at the specified x,y location.
Use larger font for 'rubric' and 'initial' types.
Use italic for 'marginalia'.
"""

        response = self.client.models.generate_content(
            model=self.model,
            contents=[prompt],
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE", "TEXT"],
            ),
        )

        # Extract image from response
        for part in response.candidates[0].content.parts:
            if hasattr(part, "inline_data") and part.inline_data:
                return part.inline_data.data

        # Fallback: return a 1x1 transparent PNG
        return self._create_empty_png()

    def _generate_clean_reconstruction(
        self,
        original_image: Path,
        page_json: Page,
        text_layer: str,
    ) -> bytes:
        """Generate clean English manuscript reconstruction."""
        # Collect translated text
        text_blocks = []
        for zone in page_json.zones_by_order():
            text = zone.text.get_layer(text_layer)
            if not text:
                text = zone.text.en_interpreted or zone.text.en_literal
            if text:
                text_blocks.append({
                    "type": zone.type,
                    "text": text,
                    "position": f"({zone.x:.2f}, {zone.y:.2f})",
                })

        # Load prompt
        try:
            prompt = _load_prompt(
                "reconstruction",
                text_content=json.dumps(text_blocks, indent=2),
                width=page_json.image.width_px,
                height=page_json.image.height_px,
            )
        except FileNotFoundError:
            prompt = f"""Create a beautiful manuscript page in English.

Style: Medieval manuscript with elegant calligraphy
Size: {page_json.image.width_px}x{page_json.image.height_px} pixels
Background: Aged parchment texture

Text content to include:
{json.dumps(text_blocks, indent=2)}

Guidelines:
- Use an elegant serif/blackletter hybrid font
- Position text blocks as indicated
- Add subtle decorative elements (borders, initials) appropriate to medieval manuscripts
- Make 'rubric' text in red
- Make 'initial' letters large and decorated
- The result should look like a genuine medieval manuscript, but in English
"""

        # Read original for reference
        image_bytes = original_image.read_bytes()
        image_part = types.Part.from_bytes(
            data=image_bytes,
            mime_type="image/jpeg"
        )

        response = self.client.models.generate_content(
            model=self.model,
            contents=[prompt, image_part],
            config=types.GenerateContentConfig(
                response_modalities=["IMAGE", "TEXT"],
            ),
        )

        # Extract generated image
        for part in response.candidates[0].content.parts:
            if hasattr(part, "inline_data") and part.inline_data:
                return part.inline_data.data

        return self._create_empty_png()

    def _composite_hybrid(
        self,
        original_bytes: bytes,
        overlay_bytes: bytes,
    ) -> bytes:
        """Composite overlay onto original image."""
        try:
            from PIL import Image

            original = Image.open(BytesIO(original_bytes)).convert("RGBA")
            overlay = Image.open(BytesIO(overlay_bytes)).convert("RGBA")

            # Resize overlay if needed
            if overlay.size != original.size:
                overlay = overlay.resize(original.size, Image.Resampling.LANCZOS)

            # Composite
            result = Image.alpha_composite(original, overlay)

            buffer = BytesIO()
            result.save(buffer, format="PNG")
            return buffer.getvalue()

        except ImportError:
            # Without Pillow, just return the overlay
            return overlay_bytes

    def _generate_scholarly_overlay(
        self,
        page_json: Page,
        text_layer: str,
    ) -> bytes:
        """Generate scholarly annotation overlay.

        Includes zone numbers, type labels, and translated text.
        """
        try:
            from PIL import Image, ImageDraw, ImageFont

            width = page_json.image.width_px
            height = page_json.image.height_px
            img = Image.new("RGBA", (width, height), (0, 0, 0, 0))
            draw = ImageDraw.Draw(img)

            try:
                font = ImageFont.truetype("arial.ttf", 14)
                font_small = ImageFont.truetype("arial.ttf", 10)
            except OSError:
                font = ImageFont.load_default()
                font_small = font

            colors = {
                "line": (0, 100, 200, 180),  # Blue
                "rubric": (200, 0, 0, 180),  # Red
                "initial": (0, 150, 0, 180),  # Green
                "marginalia": (150, 100, 0, 180),  # Brown
                "illustration": (150, 0, 150, 180),  # Purple
                "default": (100, 100, 100, 180),  # Gray
            }

            for zone in page_json.zones_by_order():
                # Calculate pixel coordinates
                x = int(zone.x * width)
                y = int(zone.y * height)
                x2 = int((zone.x + zone.width) * width)
                y2 = int((zone.y + zone.height) * height)

                # Get color for zone type
                color = colors.get(zone.type, colors["default"])

                # Draw bounding box
                draw.rectangle([x, y, x2, y2], outline=color, width=2)

                # Draw zone ID label
                label = f"{zone.zone_id} ({zone.type})"
                draw.rectangle([x, y-16, x + len(label)*7, y], fill=color)
                draw.text((x+2, y-14), label, font=font_small, fill=(255, 255, 255, 255))

                # Draw translated text (if fits)
                text = zone.text.get_layer(text_layer)
                if not text:
                    text = zone.text.en_interpreted or zone.text.en_literal
                if text and len(text) < 100:
                    # Truncate if too long
                    display_text = text[:80] + "..." if len(text) > 80 else text
                    draw.text((x+4, y+4), display_text, font=font, fill=color)

            buffer = BytesIO()
            img.save(buffer, format="PNG")
            return buffer.getvalue()

        except ImportError:
            return self._generate_text_overlay(page_json, text_layer)

    def _generate_comparison(
        self,
        original_bytes: bytes,
        overlay_bytes: bytes,
        page_json: Page,
    ) -> bytes:
        """Generate side-by-side comparison image."""
        try:
            from PIL import Image

            original = Image.open(BytesIO(original_bytes)).convert("RGB")
            overlay = Image.open(BytesIO(overlay_bytes)).convert("RGBA")

            # Composite overlay on original for right side
            original_rgba = original.convert("RGBA")
            if overlay.size != original_rgba.size:
                overlay = overlay.resize(original_rgba.size, Image.Resampling.LANCZOS)
            annotated = Image.alpha_composite(original_rgba, overlay)

            # Create side-by-side
            width = original.width
            height = original.height
            comparison = Image.new("RGB", (width * 2 + 20, height), (240, 240, 240))

            comparison.paste(original, (0, 0))
            comparison.paste(annotated.convert("RGB"), (width + 20, 0))

            buffer = BytesIO()
            comparison.save(buffer, format="PNG")
            return buffer.getvalue()

        except ImportError:
            return overlay_bytes

    def _create_empty_png(self) -> bytes:
        """Create a minimal transparent PNG."""
        # 1x1 transparent PNG
        return base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR4nGNgYGBgAAAABQABh6FO1AAAAABJRU5ErkJggg=="
        )
