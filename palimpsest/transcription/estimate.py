"""Cost estimation for transcription runs."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Optional

from PIL import Image

from palimpsest.config import DEFAULT_MODEL_VISION


# Gemini pricing per 1M tokens
# https://ai.google.dev/pricing
PRICING = {
    "gemini-2.5-flash-lite": {"input": 0.10, "output": 0.40},
    "gemini-3.1-flash-lite-preview": {"input": 0.25, "output": 1.50},
    "gemini-3-flash-preview": {"input": 0.50, "output": 3.00},
    "gemini-3.1-flash-image-preview": {"input": 0.50, "output": 3.00},
    "gemini-3-pro-image-preview": {"input": 1.25, "output": 5.00},
}

# Cache discount rate: 75% discount on cached tokens
CACHE_DISCOUNT_RATE = 0.75

# Image token estimation based on resolution
# High-res images use more tokens
TOKEN_PER_IMAGE_LOW = 258  # ~258 tokens for low-res
TOKEN_PER_IMAGE_HIGH = 1500  # ~1500 tokens for high-res (estimate)

# Average prompt/output sizes (measured from traces)
AVG_PROMPT_TOKENS = 800  # System prompt + instructions
AVG_OUTPUT_TOKENS_PASS1 = 2500  # JSON output
AVG_OUTPUT_TOKENS_PASS2 = 3000  # Refined JSON output


@dataclass
class CostEstimate:
    """Estimated cost for a transcription run."""
    pages: int
    passes: int
    model: str
    media_resolution: str
    input_tokens: int
    output_tokens: int
    cost_usd: float
    cost_with_cache_usd: Optional[float] = None
    cached_tokens: int = 0

    def summary(self) -> str:
        lines = [
            f"Pages: {self.pages}",
            f"Passes: {self.passes} ({'pass1 only' if self.passes == 1 else 'pass1 + pass2'})",
            f"Model: {self.model}",
            f"Media resolution: {self.media_resolution}",
            f"",
            f"Estimated tokens:",
            f"  Input:  {self.input_tokens:,} ({self.input_tokens / 1_000_000:.2f}M)",
            f"  Output: {self.output_tokens:,} ({self.output_tokens / 1_000_000:.2f}M)",
            f"",
        ]

        if self.cost_with_cache_usd is not None and self.cost_with_cache_usd < self.cost_usd:
            savings = self.cost_usd - self.cost_with_cache_usd
            savings_pct = (savings / self.cost_usd) * 100
            lines.extend([
                f"Estimated cost: ${self.cost_with_cache_usd:.2f} (with context caching)",
                f"  Without caching: ${self.cost_usd:.2f}",
                f"  Savings: ${savings:.2f} ({savings_pct:.0f}%)",
            ])
        else:
            lines.append(f"Estimated cost: ${self.cost_usd:.2f}")

        # Add cost-saving tips if applicable
        if self.passes == 2:
            base_cost = self.cost_with_cache_usd if self.cost_with_cache_usd else self.cost_usd
            single_pass_cost = base_cost * 0.45  # pass1 is ~45% of total
            lines.extend([
                f"",
                f"Cost-saving options:",
                f"  --pass-mode pass1: ~${single_pass_cost:.2f} (skip refinement)",
            ])

        return "\n".join(lines)


def estimate_image_tokens(image_path: Path, media_resolution: str = "high") -> int:
    """Estimate tokens for an image based on its dimensions."""
    try:
        with Image.open(image_path) as img:
            width, height = img.size
            pixels = width * height

            # Gemini uses tiles of ~768x768 for high-res
            if media_resolution.lower() == "high":
                tiles = max(1, (pixels // (768 * 768)))
                return TOKEN_PER_IMAGE_LOW + (tiles * 200)
            else:
                return TOKEN_PER_IMAGE_LOW
    except Exception:
        # Fallback estimate
        return TOKEN_PER_IMAGE_HIGH if media_resolution == "high" else TOKEN_PER_IMAGE_LOW


def _get_cache_discount(model: str) -> float:
    """Get the cache discount rate for a model."""
    if model.startswith("gemini-3"):
        return CACHE_DISCOUNT_RATE
    return 0.0  # No caching support


def estimate_cost(
    image_dir: Path,
    pattern: str = "*.jpg",
    model: str = DEFAULT_MODEL_VISION,
    pass_mode: str = "both",
    media_resolution: str = "high",
    limit: Optional[int] = None,
) -> CostEstimate:
    """Estimate the cost of transcribing images."""
    images = sorted(image_dir.glob(pattern))
    if limit:
        images = images[:limit]

    page_count = len(images)
    passes = 2 if pass_mode == "both" else 1

    # Estimate tokens per page
    if images:
        # Sample a few images to estimate token usage
        sample_size = min(5, len(images))
        sample_tokens = sum(
            estimate_image_tokens(img, media_resolution)
            for img in images[:sample_size]
        ) / sample_size
        tokens_per_image = int(sample_tokens)
    else:
        tokens_per_image = TOKEN_PER_IMAGE_HIGH if media_resolution == "high" else TOKEN_PER_IMAGE_LOW

    # Calculate total tokens
    # Pass 1: prompt + image -> output
    # Pass 2: prompt + image + draft -> output
    input_per_pass1 = AVG_PROMPT_TOKENS + tokens_per_image
    input_per_pass2 = AVG_PROMPT_TOKENS + tokens_per_image + AVG_OUTPUT_TOKENS_PASS1

    if pass_mode == "pass1":
        total_input = page_count * input_per_pass1
        total_output = page_count * AVG_OUTPUT_TOKENS_PASS1
        # Cacheable: pass1 prompt
        cached_tokens = page_count * AVG_PROMPT_TOKENS
    elif pass_mode == "pass2":
        total_input = page_count * input_per_pass2
        total_output = page_count * AVG_OUTPUT_TOKENS_PASS2
        # Cacheable: pass2 instruction (roughly half of prompt, since draft varies)
        cached_tokens = page_count * (AVG_PROMPT_TOKENS // 2)
    else:  # both
        total_input = page_count * (input_per_pass1 + input_per_pass2)
        total_output = page_count * (AVG_OUTPUT_TOKENS_PASS1 + AVG_OUTPUT_TOKENS_PASS2)
        # Cacheable: pass1 prompt + pass2 instruction
        cached_tokens = page_count * (AVG_PROMPT_TOKENS + AVG_PROMPT_TOKENS // 2)

    # Look up pricing
    pricing = PRICING.get(model, PRICING[DEFAULT_MODEL_VISION] if DEFAULT_MODEL_VISION in PRICING else PRICING["gemini-3.1-flash-lite-preview"])
    input_cost = (total_input / 1_000_000) * pricing["input"]
    output_cost = (total_output / 1_000_000) * pricing["output"]
    total_cost = input_cost + output_cost

    # Calculate cost with caching
    cache_discount = _get_cache_discount(model)
    cost_with_cache = None
    if cache_discount > 0 and page_count > 1:
        # Cached tokens are discounted
        non_cached_input = total_input - cached_tokens
        cached_input_cost = (cached_tokens / 1_000_000) * pricing["input"] * (1 - cache_discount)
        non_cached_input_cost = (non_cached_input / 1_000_000) * pricing["input"]
        cost_with_cache = cached_input_cost + non_cached_input_cost + output_cost

    return CostEstimate(
        pages=page_count,
        passes=passes,
        model=model,
        media_resolution=media_resolution,
        input_tokens=total_input,
        output_tokens=total_output,
        cost_usd=total_cost,
        cost_with_cache_usd=cost_with_cache,
        cached_tokens=cached_tokens if cache_discount > 0 else 0,
    )
