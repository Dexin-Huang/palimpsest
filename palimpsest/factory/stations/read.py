"""read: VLM transcription, routed by the segment station's decision.

- ``blank``      → empty transcription, zero tokens spent
- ``full_page``  → one call on the whole cleaned image (the light-page path);
                   if the model hits its output-token ceiling, the page
                   escalates to the segmented path automatically
- ``segmented``  → each region is lifted onto a white square tile (in memory,
                   padded, high effective resolution) and read in its own
                   bounded call; the page text is composed in reading order

Either way the output is ONE page_transcription artifact carrying the
per-region texts and geometry, so nothing downstream changes shape.
"""

from __future__ import annotations

import re

import numpy as np

from palimpsest.factory.usage import combine_cost, combine_count
from palimpsest.factory.core.registry import register
from palimpsest.factory.core.station import Job, Station, StationResult
from palimpsest.factory.gateway import (
    GatewayError,
    ImageContent,
    ModelRequest,
    generate_json,
)
from palimpsest.factory.imaging import encode_png
from palimpsest.factory.stations.image_input import load_image
from palimpsest.factory.workspace.io import read_json

DEFAULT_SYSTEM_PROMPT = (
    "You are an expert paleographer transcribing digitized manuscript pages."
)
TILE_PAD_GLYPHS = 1.5
_TRUNCATION_REASONS = ("MAX_TOKENS", "LENGTH", "INCOMPLETE")

# Thinking models sometimes deliberate in their output; a schema leaves no
# channel for anything but the transcription itself.
READ_SCHEMA = {
    "type": "object",
    "properties": {"transcription": {"type": "string"}},
    "required": ["transcription"],
}


class Read(Station):
    name = "read"

    grain = "page"
    consumes = ("page_image_clean", "page_regions")
    produces = "page_transcription"
    uses_model = True
    param_keys = frozenset(
        {
            "system",
            "temperature",
            "max_output_tokens",
            "media_resolution",
            "thinking_level",
        }
    )
    production_dependencies = (
        "factory/gateway/__init__.py",
        "factory/gateway/client.py",
        "factory/gateway/gemini.py",
        "factory/gateway/omp_codex.py",
        "factory/gateway/pricing.py",
        "factory/gateway/protocol.py",
        "factory/imaging.py",
        "factory/stations/image_input.py",
        "factory/usage.py",
    )

    def run(self, job: Job) -> StationResult:
        plan = read_json(job.path_of("page_regions"))
        usage = _Usage()

        try:
            route = plan["route"]
            if route == "blank":
                regions, text = [], ""
            elif route == "full_page":
                regions, text, escalated = self._full_page(job, plan, usage)
                if escalated:
                    route = "segmented(escalated)"
            else:
                regions, text = self._segmented(job, plan, usage)
        except GatewayError as error:
            raise error.with_prior_usage(
                tokens_in=usage.tokens_in,
                tokens_out=usage.tokens_out,
                cost_usd=usage.cost,
            ) from error

        return StationResult(
            payload={
                "doc_id": job.doc_id,
                "page_id": job.page_id,
                "page_seq": job.page.get("order", 0),
                "canvas_id": job.page.get("canvas_id", ""),
                "text": text,
                "route": route,
                "regions": regions,
            },
            tokens_in=usage.tokens_in,
            tokens_out=usage.tokens_out,
            cost_usd=usage.cost,
        )

    def _full_page(self, job: Job, plan: dict, usage: "_Usage"):
        text, response = self._call(job, (job.path_of("page_image_clean"),), usage)
        truncated = response.finish_reason and any(
            reason in response.finish_reason.upper() for reason in _TRUNCATION_REASONS
        )
        if truncated:
            # dense page mis-routed: escalate rather than keep a truncation
            regions, text = self._segmented(job, plan, usage)
            return regions, text, True
        return [], text, False

    def _segmented(self, job: Job, plan: dict, usage: "_Usage"):
        image = load_image(job, "page_image_clean")
        glyph = max(4, int(plan.get("glyph_height_px", 12)))
        pad = int(glyph * TILE_PAD_GLYPHS)

        regions_out = []
        texts = []
        ordered = sorted(plan["regions"], key=lambda r: r["reading_order"])
        for region in ordered:
            tile = _tile(image, region["bbox"], pad)
            entry = {
                "region_id": region["region_id"],
                "kind": region["kind"],
                "bbox": region["bbox"],
                "text": "",
            }
            try:
                entry["text"], _ = self._call(
                    job,
                    (ImageContent(encode_png(tile)),),
                    usage,
                    max_tokens=_tile_token_cap(region["est_lines"]),
                )
            except GatewayError as error:
                # a pathological tile (model loops on damaged/hyper-abbreviated
                # script) becomes an auditable hole, not a dead page
                entry["error"] = str(error)
                usage.add_error(error)
            regions_out.append(entry)
            if entry["text"]:
                texts.append(
                    f"[margin] {entry['text']}"
                    if region["kind"] == "marginalia"
                    else entry["text"]
                )
        return regions_out, "\n\n".join(texts)

    def _call(
        self, job: Job, images: tuple, usage: "_Usage", max_tokens: int | None = None
    ):
        params = job.config.params
        value, response = generate_json(
            ModelRequest(
                model=job.config.model,
                prompt=job.config.prompt.text,
                system=params.get("system", DEFAULT_SYSTEM_PROMPT),
                images=images,
                temperature=params.get("temperature", 0.1),
                max_output_tokens=max_tokens or params.get("max_output_tokens", 32768),
                media_resolution=params.get("media_resolution"),
                json_output=True,
                json_schema=READ_SCHEMA,
                thinking_level=params.get("thinking_level"),
            )
        )
        usage.add(response)
        return _sanitize(value["transcription"]), response


class _Usage:
    def __init__(self) -> None:
        self.tokens_in = 0
        self.tokens_out = 0
        self.cost = 0.0

    def add(self, response) -> None:
        self.tokens_in = combine_count(self.tokens_in, response.prompt_tokens)
        self.tokens_out = combine_count(
            self.tokens_out, response.billable_output_tokens
        )
        self.cost = combine_cost(self.cost, response.cost_usd)

    def add_error(self, error: GatewayError) -> None:
        self.tokens_in = combine_count(self.tokens_in, error.tokens_in)
        self.tokens_out = combine_count(self.tokens_out, error.tokens_out)
        self.cost = combine_cost(self.cost, error.cost_usd)


_TRAILING_JUNK = re.compile(r"[\s`{}\[\]<>|~^\\_]+$")
_TRAILING_ESCAPE = re.compile(r"(?:/n|\\n)$")


def _sanitize(text: str) -> str:
    """Strip model-noise tails (stray escapes/braces like '/n_`}') that
    sometimes trail the transcription string. Only trailing junk runs are
    touched — page content is never edited."""
    text = text.strip()
    while True:
        cleaned = _TRAILING_ESCAPE.sub("", _TRAILING_JUNK.sub("", text))
        if cleaned == text:
            return text
        text = cleaned


def _tile_token_cap(est_lines: int) -> int:
    """A tile's transcription is bounded by its line count; a runaway model
    loop should hit a cheap ceiling, not burn 32k tokens before failing."""
    return min(4000, max(800, est_lines * 80 + 400))


def _tile(image: np.ndarray, bbox: list[int], pad: int) -> np.ndarray:
    """Lift a region onto a white square canvas — the polygon lasso payoff."""
    h, w = image.shape[:2]
    x, y, bw, bh = bbox
    x0, y0 = max(0, x - pad), max(0, y - pad)
    x1, y1 = min(w, x + bw + pad), min(h, y + bh + pad)
    crop = image[y0:y1, x0:x1]
    ch, cw = crop.shape[:2]
    side = max(ch, cw)
    tile = np.full((side, side, 3), 255, np.uint8)
    oy, ox = (side - ch) // 2, (side - cw) // 2
    tile[oy : oy + ch, ox : ox + cw] = crop
    return tile


register(Read())
