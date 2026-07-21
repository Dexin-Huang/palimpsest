"""Trusted, versioned response schemas for evaluation model judges."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Literal

from palimpsest.factory.evaluation.candidate import RecordError

PAIRWISE_PREFERENCE_V1 = "pairwise_preference/v1"
_FAILURE_FLAGS = frozenset(
    {
        "image_unreadable",
        "output_a_unreadable",
        "output_b_unreadable",
        "insufficient_visible_evidence",
    }
)


@dataclass(frozen=True, slots=True)
class PairwisePreference:
    winner: Literal["A", "B", "tie"]
    confidence: float
    reason: str
    failure_flags: tuple[str, ...] = ()


@dataclass(frozen=True, slots=True)
class ResponseSchema:
    name: str
    json_schema: Mapping[str, object]

    def validate(self, value: object) -> PairwisePreference:
        if not isinstance(value, dict):
            raise RecordError(f"{self.name} response must be an object")
        allowed = {"winner", "confidence", "reason", "failure_flags"}
        required = {"winner", "confidence", "reason"}
        unknown = set(value) - allowed
        missing = required - set(value)
        if unknown:
            raise RecordError(f"Unknown {self.name} response keys: {sorted(unknown)}")
        if missing:
            raise RecordError(f"Missing {self.name} response keys: {sorted(missing)}")

        winner = value["winner"]
        if winner not in {"A", "B", "tie"}:
            raise RecordError(f"{self.name}.winner must be A, B, or tie")
        confidence = value["confidence"]
        if isinstance(confidence, bool) or not isinstance(confidence, int | float):
            raise RecordError(f"{self.name}.confidence must be a number")
        confidence = float(confidence)
        if not 0.0 <= confidence <= 1.0:
            raise RecordError(f"{self.name}.confidence must be between zero and one")
        reason = value["reason"]
        if not isinstance(reason, str) or not reason.strip():
            raise RecordError(f"{self.name}.reason must be a non-empty string")
        reason = reason.strip()
        if len(reason) > 500:
            raise RecordError(f"{self.name}.reason must contain at most 500 characters")

        raw_flags = value.get("failure_flags", [])
        if not isinstance(raw_flags, list):
            raise RecordError(f"{self.name}.failure_flags must be an array")
        if any(
            not isinstance(flag, str) or flag not in _FAILURE_FLAGS
            for flag in raw_flags
        ):
            raise RecordError(f"{self.name}.failure_flags contains an unknown flag")
        if len(set(raw_flags)) != len(raw_flags):
            raise RecordError(f"{self.name}.failure_flags must not contain duplicates")
        return PairwisePreference(winner, confidence, reason, tuple(raw_flags))


PAIRWISE_PREFERENCE_SCHEMA_V1 = ResponseSchema(
    name=PAIRWISE_PREFERENCE_V1,
    json_schema=MappingProxyType(
        {
            "type": "object",
            "additionalProperties": False,
            "properties": {
                "winner": {"type": "string", "enum": ["A", "B", "tie"]},
                "confidence": {"type": "number", "minimum": 0.0, "maximum": 1.0},
                "reason": {"type": "string", "minLength": 1, "maxLength": 500},
                "failure_flags": {
                    "type": "array",
                    "uniqueItems": True,
                    "items": {"type": "string", "enum": sorted(_FAILURE_FLAGS)},
                },
            },
            "required": ["winner", "confidence", "reason"],
        }
    ),
)


def trusted_response_schemas() -> Mapping[str, ResponseSchema]:
    """Return the fixed schemas judge records are allowed to reference."""

    return MappingProxyType(
        {PAIRWISE_PREFERENCE_SCHEMA_V1.name: PAIRWISE_PREFERENCE_SCHEMA_V1}
    )
