"""Gemini context caching for prompt reuse."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional

from google import genai
from google.genai import types

MIN_CACHEABLE_PROMPT_CHARS = 4096


@dataclass
class CacheManager:
    """Manages Gemini context caches for a transcription run.

    Context caching reduces costs by reusing precomputed tokens for repeated prompts.
    Gemini 3 provides a 75% discount on cached tokens.
    """

    client: genai.Client
    model: str
    pass1_cache_name: Optional[str] = field(default=None)
    pass2_cache_name: Optional[str] = field(default=None)
    _created_caches: list[str] = field(default_factory=list)

    def create_pass1_cache(self, prompt: str, ttl_hours: int = 2) -> str:
        """Create cache for Pass 1 prompt.

        Args:
            prompt: The complete Pass 1 prompt text
            ttl_hours: Time-to-live in hours (default 2)

        Returns:
            The cache name for use in API calls
        """
        cache = self.client.caches.create(
            model=self.model,
            config=types.CreateCachedContentConfig(
                display_name=f"pass1_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                contents=[types.Content(parts=[types.Part(text=prompt)], role="user")],
                ttl=f"{ttl_hours * 3600}s",
            ),
        )
        self.pass1_cache_name = cache.name
        self._created_caches.append(cache.name)
        return cache.name

    def create_pass2_cache(self, instruction: str, ttl_hours: int = 2) -> str:
        """Create cache for Pass 2 system instruction.

        For Pass 2, we cache the instruction portion as a system_instruction,
        since the draft transcription varies per page.

        Args:
            instruction: The cacheable instruction text (without draft placeholder)
            ttl_hours: Time-to-live in hours (default 2)

        Returns:
            The cache name for use in API calls
        """
        cache = self.client.caches.create(
            model=self.model,
            config=types.CreateCachedContentConfig(
                display_name=f"pass2_{datetime.now().strftime('%Y%m%d_%H%M%S')}",
                system_instruction=instruction,
                ttl=f"{ttl_hours * 3600}s",
            ),
        )
        self.pass2_cache_name = cache.name
        self._created_caches.append(cache.name)
        return cache.name

    def delete_caches(self) -> None:
        """Clean up caches after run.

        Safe to call even if caches have expired or don't exist.
        """
        for cache_name in self._created_caches:
            try:
                self.client.caches.delete(name=cache_name)
            except Exception:
                pass  # Cache may have expired or already been deleted
        self._created_caches.clear()
        self.pass1_cache_name = None
        self.pass2_cache_name = None


def extract_pass2_instruction(template: str) -> Optional[str]:
    """Extract cacheable instruction from pass2 template.

    The Pass 2 prompt contains a template like:
        You are refining a transcription...
        {draft_transcription}
        Please improve...

    We split this into instruction (cacheable) and draft (variable).

    Args:
        template: The Pass 2 prompt template with {draft_transcription} placeholder

    Returns:
        The cacheable instruction text, or None if placeholder not found
    """
    marker = "{draft_transcription}"
    if marker not in template:
        return None

    # Get instruction before and after the marker
    before, after = template.split(marker, 1)

    # Combine as system instruction with placeholder note
    instruction_parts = []
    if before.strip():
        instruction_parts.append(before.strip())
    instruction_parts.append("[DRAFT TRANSCRIPTION WILL BE PROVIDED IN USER MESSAGE]")
    if after.strip():
        instruction_parts.append(after.strip())

    return "\n\n".join(instruction_parts)


def is_caching_supported(model: str) -> bool:
    """Check if the model supports context caching.

    Args:
        model: The model name/ID

    Returns:
        True if the model supports caching
    """
    return model.startswith("gemini-3")


def is_cacheable_text(text: str) -> bool:
    """Return True when a prompt is large enough to justify a cache attempt.

    Gemini cache creation currently rejects very small prompts. A conservative
    character threshold avoids noisy INVALID_ARGUMENT responses on scout runs.
    """
    return len((text or "").strip()) >= MIN_CACHEABLE_PROMPT_CHARS
