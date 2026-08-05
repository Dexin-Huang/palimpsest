"""Seed-label the unlabeled glyph exemplar bank, the Vesuvius bootstrap move.

The char_inventory harvest produced 1,959 glyph crops with geometry only
(crop_id, page, column, position, bbox) and no text labels. This instrument
produces SEED labels - one open-vocabulary reading per crop by the same
model identity the validated blind adjudicator uses - so the exemplar bank
becomes a training and verification asset. Seed labels are explicitly not
gold: the record marks them development-only, and the hardening loop
(model-vs-model disagreement review, expert verification through the draft
queue) is the declared follow-up, mirroring the Vesuvius iterative
pseudo-label loop.

- ``label``: resumable, budget-capped pass over every crop in the index.
- ``summarize``: distribution, certainty mix, and per-page coverage.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import sys
from collections import Counter
from pathlib import Path
from time import perf_counter

import cv2

from palimpsest.factory.gateway.client import generate_json
from palimpsest.factory.gateway.protocol import GatewayError, ImageContent, ModelRequest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
INVENTORY_ROOT = REPOSITORY_ROOT / "experiments/char_inventory/out"
LABELER_MODEL = "gemini-3.5-flash"
MIN_SIDE = 256

SCHEMA = {
    "type": "object",
    "properties": {
        "character": {"type": "string"},
        "certainty": {"type": "string", "enum": ["high", "medium", "low"]},
        "notes": {"type": "string"},
    },
    "required": ["character", "certainty", "notes"],
    "additionalProperties": False,
}

PROMPT = """This image is ONE character cropped from a premodern Chinese manuscript written in ink with a brush (Dunhuang-style hand).

Identify the character. Rules:
- Answer with EXACTLY ONE character in the character field, choosing the Unicode codepoint whose canonical glyph most closely matches the written form (variant forms keep their own codepoints; do not normalize).
- If the crop is too degraded, truncated, or ambiguous to commit to one character, return an empty character field and say why in notes.
- certainty: high only when the strokes are complete and unambiguous; medium when plausible alternatives exist; low when mostly guessing.
- notes: one short clause (deciding feature, or the reason it is illegible).

Respond as JSON only."""


def load_index() -> list[dict[str, object]]:
    records = json.loads((INVENTORY_ROOT / "index.json").read_text(encoding="utf-8"))
    if not isinstance(records, list) or not records:
        raise RuntimeError("char_inventory index is empty or malformed")
    return records


def crop_bytes(crop_id: str) -> bytes | None:
    path = INVENTORY_ROOT / "crops" / f"{crop_id}.png"
    image = cv2.imread(str(path), cv2.IMREAD_COLOR)
    if image is None:
        return None
    long_side = max(image.shape[:2])
    if long_side < MIN_SIDE:
        scale = MIN_SIDE / long_side
        image = cv2.resize(
            image,
            (
                max(1, round(image.shape[1] * scale)),
                max(1, round(image.shape[0] * scale)),
            ),
            interpolation=cv2.INTER_CUBIC,
        )
    ok, encoded = cv2.imencode(".png", image)
    return encoded.tobytes() if ok else None


def cmd_label(args: argparse.Namespace) -> None:
    records = load_index()
    done: set[str] = set()
    spent = 0.0
    if args.output.exists():
        for line in args.output.read_text(encoding="utf-8").splitlines():
            row = json.loads(line)
            done.add(row["crop_id"])
            spent += float(row.get("cost_usd") or 0.0)
    prompt_sha = hashlib.sha256(PROMPT.encode("utf-8")).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    pending = [r for r in records if str(r["crop_id"]) not in done]
    for index, record in enumerate(pending, 1):
        if spent >= args.max_cost:
            raise SystemExit(
                f"budget stop: observed spend {spent:.6f} USD >= {args.max_cost} USD"
            )
        crop_id = str(record["crop_id"])
        started = perf_counter()
        payload = crop_bytes(crop_id)
        if payload is None:
            value, response = (
                {"character": "", "certainty": "low", "notes": "crop file unreadable"},
                None,
            )
        else:
            try:
                value, response = generate_json(
                    ModelRequest(
                        model=LABELER_MODEL,
                        prompt=PROMPT,
                        system=(
                            "You are a paleography assistant identifying single "
                            "handwritten Chinese characters from crops."
                        ),
                        images=(ImageContent(data=payload, mime="image/png"),),
                        temperature=0.0,
                        max_output_tokens=512,
                        media_resolution="high",
                        json_output=True,
                        json_schema=SCHEMA,
                        thinking_level="minimal",
                    )
                )
            except GatewayError as error:
                value, response = (
                    {"character": "", "certainty": "low", "notes": f"gateway: {error}"},
                    None,
                )
        character = str(value.get("character") or "")
        if len(character) > 1:
            character = character[0]
        row = {
            "crop_id": crop_id,
            "page": record.get("page"),
            "column": record.get("column"),
            "position": record.get("position"),
            "bbox": record.get("bbox"),
            "labeler_model": LABELER_MODEL,
            "prompt_sha256": prompt_sha,
            "character": character,
            "certainty": value.get("certainty"),
            "notes": value.get("notes"),
            "failed": response is None,
            "cost_usd": None if response is None else response.cost_usd,
            "latency_seconds": perf_counter() - started,
        }
        with args.output.open("a", encoding="utf-8", newline="\n") as target:
            target.write(json.dumps(row, ensure_ascii=False, separators=(",", ":")))
            target.write("\n")
        spent += float(row["cost_usd"] or 0.0)
        print(
            f"{index}/{len(pending)} {crop_id}: {character or '(illegible)'} "
            f"[{row['certainty']}] {row['cost_usd'] or 0.0:.6f} USD",
            flush=True,
        )


def cmd_summarize(args: argparse.Namespace) -> None:
    rows = [
        json.loads(line)
        for line in args.labels.read_text(encoding="utf-8").splitlines()
    ]
    labeled = [r for r in rows if r["character"]]
    certainty = Counter(r["certainty"] for r in rows)
    chars = Counter(r["character"] for r in labeled)
    pages = Counter(r["page"] for r in rows)
    cost = sum(float(r.get("cost_usd") or 0.0) for r in rows)
    print(
        json.dumps(
            {
                "crops": len(rows),
                "labeled": len(labeled),
                "illegible_or_failed": len(rows) - len(labeled),
                "distinct_characters": len(chars),
                "certainty": dict(certainty),
                "failed_calls": sum(1 for r in rows if r["failed"]),
                "top_characters": chars.most_common(15),
                "pages": dict(pages),
                "cost_usd": round(cost, 6),
            },
            ensure_ascii=False,
            indent=2,
        )
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    label = sub.add_parser("label")
    label.add_argument("--output", type=Path, required=True)
    label.add_argument("--max-cost", type=float, required=True)
    label.set_defaults(handler=cmd_label)
    summarize = sub.add_parser("summarize")
    summarize.add_argument("--labels", type=Path, required=True)
    summarize.set_defaults(handler=cmd_summarize)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    args.handler(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
