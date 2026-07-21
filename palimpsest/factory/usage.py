"""Token and cost aggregation with explicit unknown values."""

from __future__ import annotations


def combine_count(total: int | None, value: int | None) -> int | None:
    if total is None or value is None:
        return None
    return total + value


def combine_cost(total: float | None, value: float | None) -> float | None:
    if total is None or value is None:
        return None
    return total + value
