"""Station-owned evaluation metric registrations."""

from palimpsest.factory.evaluation.metrics import MetricRegistry

from .deterministic import register_deterministic_metrics
from .editorial import register_editorial_metrics
from .imaging import register_imaging_metrics
from .language import register_language_metrics

from .read import (
    CONTAMINATION_TERMS,
    CharacterEdits,
    character_edits,
    character_error_structure,
    contamination_hits,
    contamination_rate,
    empty_output_rate,
    han_variant_v1_character_error_rate,
    han_variant_v1_partial_gold_character_error_rate,
    recognized_text_v1_character_error_rate,
    recognized_text_v1_partial_gold_character_error_rate,
    normalize_diplomatic,
    normalized_character_error_rate,
    partial_gold_character_error_rate,
    page_completeness,
    region_completeness,
    register_read_metrics,
    repetition_rate,
)


def register_station_metrics(registry: MetricRegistry) -> None:
    """Register every trusted built-in station metric exactly once."""

    for registration in (
        register_read_metrics,
        register_deterministic_metrics,
        register_imaging_metrics,
        register_language_metrics,
        register_editorial_metrics,
    ):
        registration(registry)


__all__ = [
    "CONTAMINATION_TERMS",
    "CharacterEdits",
    "character_edits",
    "character_error_structure",
    "contamination_hits",
    "contamination_rate",
    "empty_output_rate",
    "han_variant_v1_character_error_rate",
    "han_variant_v1_partial_gold_character_error_rate",
    "invented_character_rate",
    "normalize_diplomatic",
    "normalized_character_error_rate",
    "partial_gold_character_error_rate",
    "recognized_text_v1_character_error_rate",
    "recognized_text_v1_partial_gold_character_error_rate",
    "page_completeness",
    "region_completeness",
    "register_deterministic_metrics",
    "register_editorial_metrics",
    "register_imaging_metrics",
    "register_language_metrics",
    "register_read_metrics",
    "register_station_metrics",
    "repetition_rate",
]
