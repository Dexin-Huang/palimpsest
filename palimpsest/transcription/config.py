from dataclasses import dataclass
from typing import Optional

from palimpsest.config import DEFAULT_MODEL_VISION

from .prompts import load_prompt_pair

DEFAULT_MODEL = DEFAULT_MODEL_VISION


@dataclass(frozen=True)
class PromptConfig:
    prompt_name: Optional[str] = None
    prompt_set: Optional[str] = None
    prompt_pass1: Optional[str] = None
    prompt_pass2: Optional[str] = None

    def load(self) -> tuple[str, str]:
        return load_prompt_pair(
            prompt_name=self.prompt_name,
            prompt_set=self.prompt_set,
            prompt_pass1=self.prompt_pass1,
            prompt_pass2=self.prompt_pass2,
        )

    def is_valid(self) -> bool:
        return bool(self.prompt_name or self.prompt_set or (self.prompt_pass1 and self.prompt_pass2))


@dataclass(frozen=True)
class RunConfig:
    prompt: PromptConfig
    model: str = DEFAULT_MODEL
    output_format: str = "json"
    pass_mode: str = "both"
    skip_existing: bool = False
    verbose: bool = False
    delay: float = 2.0
    workers: int = 10
    max_attempts: int = 3
    trace: bool = True
    auto_skip_non_text: bool = False
    shard_count: int = 1
    shard_index: int = 0
    use_cache: bool = True  # Enable Gemini context caching for cost savings
    cache_ttl_hours: int = 2  # Cache time-to-live in hours
