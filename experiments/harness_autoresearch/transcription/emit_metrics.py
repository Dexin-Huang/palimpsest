from __future__ import annotations

import json
import math
import sys
from pathlib import Path
from typing import Any


def _number(value: Any, *, field: str) -> float:
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ValueError(f"{field} is not numeric")
    result = float(value)
    if not math.isfinite(result):
        raise ValueError(f"{field} is not finite")
    return result


def main() -> None:
    if len(sys.argv) != 2:
        raise SystemExit("usage: emit_metrics.py REPORT.json")
    report = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    aggregates = report["aggregates"]

    observations = aggregates["metrics"]["character_error_rate"]["observations"]
    character_error_rates = [
        _number(item["candidate"], field="candidate character_error_rate")
        for item in observations
        if item.get("candidate") is not None
    ]
    if len(character_error_rates) != 1:
        raise ValueError("expected one completed challenger character_error_rate")
    quality = 1.0 - max(0.0, min(1.0, character_error_rates[0]))

    hard_limits = aggregates["hard_limits"]
    hard_limit_status = float(
        bool(hard_limits)
        and all(item.get("decision") == "pass" for item in hard_limits)
    )

    operations = aggregates["operations"]
    challenger = operations["challenger"]
    cost_unknown = bool(operations["unknown_cost"]["challenger"])
    cost = (
        -1.0
        if cost_unknown
        else _number(challenger["total_cost_usd"], field="challenger total cost")
    )
    latency = _number(
        challenger["mean_latency_seconds"], field="challenger mean latency"
    )

    print(f"METRIC quality={quality:.12g}")
    print(f"METRIC hard_limit_pass={hard_limit_status:.0f}")
    print(f"METRIC cost_usd={cost:.12g}")
    print(f"METRIC latency_seconds={latency:.12g}")


if __name__ == "__main__":
    main()
