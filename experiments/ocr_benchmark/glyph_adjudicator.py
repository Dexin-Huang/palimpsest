"""Glyph-crop adjudication: is the variant-form residue decidable at crop resolution?

The v4 autopsy showed the largest wrong-class is codepoint form choice
(variant versus standard) that full-page reading cannot resolve: the exact-form
prompt cut its named pairs 44 percent but introduced reverse-overcorrections.
This instrument re-poses each disagreement at the resolution where the answer
lives: the character's own gold-annotated box, enlarged, with a blind forced
choice between the two written forms. Gold-box localization deliberately
isolates decidability from localization; a production pass would substitute
detector boxes.

- ``collect``: align stored champion outputs against gold, emit one instance
  per single-character replace (disagreement) plus a seeded control sample of
  agreement positions on confusable characters (overcorrection measurement).
- ``adjudicate``: blind forced choice per instance crop; A/B assignment is
  seeded per instance so neither position nor identity leaks which side is
  gold. Resumable, budget-capped.
- ``score``: fix and break rates, and the real patched sequence recall per
  page after applying adjudicated fixes to the stored outputs.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import unicodedata
from collections import Counter
from pathlib import Path
from time import perf_counter

import cv2
from rapidfuzz.distance import Levenshtein

from palimpsest.factory.gateway.client import generate_json
from palimpsest.factory.gateway.protocol import GatewayError, ImageContent, ModelRequest

REPOSITORY_ROOT = Path(__file__).resolve().parents[2]
TEST_ROOT = REPOSITORY_ROOT / "scratch/ocr_benchmark/mthv2/test-800-image-only-v1"
GOLD_ROOT = (
    REPOSITORY_ROOT / "palimpsest/factory/evaluation/gold/transcribe/mthv2-development"
)
ADJUDICATOR_MODEL = "gemini-3.5-flash"

SCHEMA = {
    "type": "object",
    "properties": {
        "written_form": {"type": "string", "enum": ["A", "B", "neither", "illegible"]},
        "reasoning": {"type": "string"},
    },
    "required": ["written_form", "reasoning"],
    "additionalProperties": False,
}

PROMPT = """This image shows ONE character cropped from a digitized premodern Chinese page.

Decide which exact written form appears, judging ONLY the visible strokes of the glyph as drawn on the page. Do not consider which form is more common in modern text, and do not consider meaning; compare stroke-level shape against each candidate codepoint's canonical glyph.

A: {form_a}
B: {form_b}

If the drawn glyph matches neither candidate form, answer neither. If the crop is too degraded or truncated to decide, answer illegible. Keep reasoning to one short sentence naming the deciding stroke feature. Respond as JSON only."""


def no_space(text: str) -> str:
    normalized = unicodedata.normalize(
        "NFC", text.replace("\r\n", "\n").replace("\r", "\n")
    )
    return "".join(ch for ch in normalized if not ch.isspace())


def is_cjk(ch: str) -> bool:
    code = ord(ch)
    return (
        0x3400 <= code <= 0x4DBF
        or 0x4E00 <= code <= 0x9FFF
        or 0xF900 <= code <= 0xFAFF
        or 0x20000 <= code <= 0x3134F
    )


def load_pages() -> dict[str, dict[str, object]]:
    """Map suite case_id -> original corpus/stem/image plus champion output."""

    by_token: dict[str, dict[str, object]] = {}
    for line in (
        (TEST_ROOT / "localization/gold.jsonl").read_text(encoding="utf-8").splitlines()
    ):
        record = json.loads(line)
        corpus = str(record["strata"][0])
        stem = str(record["case_id"]).rsplit("/", 1)[-1]
        token = f"{corpus.lower()}-{stem}".replace("_", "-")
        by_token[f"mthv2-{token}-transcribe-development"] = {
            "corpus": corpus,
            "stem": stem,
            "image": str(record["image"]),
        }
    return by_token


def champion_outputs(
    run_ids: list[str], fingerprint: str
) -> dict[str, dict[str, object]]:
    rows: dict[str, dict[str, object]] = {}
    for run_id in run_ids:
        report = json.loads(
            (
                REPOSITORY_ROOT / "library/evaluations/runs" / run_id / "report.json"
            ).read_text(encoding="utf-8")
        )
        for case in report["cases"]:
            for side in ("baseline", "challenger"):
                entry = case[side]
                if (
                    entry["candidate_fingerprint"] != fingerprint
                    or not entry["succeeded"]
                ):
                    continue
                payload = json.loads(
                    Path(entry["output_path"]).read_text(encoding="utf-8")
                )
                combined = str(payload["text"])
                commentary = str(payload.get("commentary", ""))
                if commentary:
                    combined += "\n" + commentary
                rows[case["case_id"]] = {
                    "run_id": run_id,
                    "output_fingerprint": entry["output_fingerprint"],
                    "combined": combined,
                }
    return rows


def parse_label_char(path: Path) -> list[tuple[str, tuple[float, float, float, float]]]:
    entries: list[tuple[str, tuple[float, float, float, float]]] = []
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        parts = raw.strip().split()
        if len(parts) != 5 or len(parts[0]) != 1:
            continue
        x1, y1, x2, y2 = (float(v) for v in parts[1:])
        entries.append((parts[0], (min(x1, x2), min(y1, y2), max(x1, x2), max(y1, y2))))
    return entries


def parse_label_textline(
    path: Path,
) -> list[tuple[str, tuple[float, float, float, float]]]:
    lines: list[tuple[str, tuple[float, float, float, float]]] = []
    for raw in path.read_text(encoding="utf-8-sig").splitlines():
        raw = raw.strip()
        if not raw:
            continue
        text, *coords = raw.split(",")
        text = text.strip()
        if not text:
            continue
        values = [float(v) for v in coords if v.strip()]
        if len(values) < 8:
            continue
        xs, ys = values[0::2], values[1::2]
        lines.append((text, (min(xs), min(ys), max(xs), max(ys))))
    return lines


def locate(
    line_text: str,
    offset: int,
    line_box: tuple[float, float, float, float],
    chars: list[tuple[str, tuple[float, float, float, float]]],
    target: str,
) -> tuple[tuple[float, float, float, float], str]:
    """Box for the offset-th character of one gold line; char-level when possible."""

    lx1, ly1, lx2, ly2 = line_box
    width, height = lx2 - lx1, ly2 - ly1
    vertical = height >= width
    pad_x, pad_y = 0.15 * width + 8, 0.15 * height + 8
    inside = [
        box
        for label, box in chars
        if label == target
        and lx1 - pad_x <= (box[0] + box[2]) / 2 <= lx2 + pad_x
        and ly1 - pad_y <= (box[1] + box[3]) / 2 <= ly2 + pad_y
    ]
    inside.sort(
        key=lambda box: (box[1] + box[3]) / 2 if vertical else -(box[0] + box[2]) / 2
    )
    occurrence = line_text[:offset].count(target)
    if occurrence < len(inside):
        return inside[occurrence], "char"
    total = max(len(line_text), 1)
    if vertical:
        y1 = ly1 + height * offset / total
        y2 = ly1 + height * (offset + 1) / total
        return (lx1, y1, lx2, y2), "interpolated"
    x2 = lx2 - width * offset / total
    x1 = lx2 - width * (offset + 1) / total
    return (x1, ly1, x2, ly2), "interpolated"


def gold_line_map(gold: str) -> tuple[str, list[tuple[int, int]]]:
    """No-space gold plus per-character (line_index, line_offset)."""

    positions: list[tuple[int, int]] = []
    pieces: list[str] = []
    for line_index, line in enumerate(gold.split("\n")):
        stripped = no_space(line)
        pieces.append(stripped)
        positions.extend((line_index, k) for k in range(len(stripped)))
    return "".join(pieces), positions


def cmd_collect(args: argparse.Namespace) -> None:
    pages = load_pages()
    outputs = champion_outputs(args.runs, args.fingerprint)
    instances: list[dict[str, object]] = []
    controls_pool: list[dict[str, object]] = []
    pair_counts: Counter[tuple[str, str]] = Counter()
    skipped_noncjk = 0

    for case_id, output in sorted(outputs.items()):
        page = pages[case_id]
        token = case_id.removesuffix("-transcribe-development").removeprefix("mthv2-")
        gold_matches = [
            p for p in GOLD_ROOT.glob("*.json") if p.stem.lower() == token.lower()
        ]
        if len(gold_matches) != 1:
            raise FileNotFoundError(f"gold not found for {case_id}")
        gold = str(json.loads(gold_matches[0].read_text(encoding="utf-8"))["text"])
        gold_ns, positions = gold_line_map(gold)
        gold_lines = [no_space(line) for line in gold.split("\n")]
        out_ns = no_space(str(output["combined"]))
        label_dir = TEST_ROOT / "assets" / str(page["corpus"])
        textlines = parse_label_textline(
            label_dir / "label_textline" / f"{page['stem']}.txt"
        )
        chars = parse_label_char(label_dir / "label_char" / f"{page['stem']}.txt")
        textline_texts = [no_space(text) for text, _ in textlines]
        aligned_lines = textline_texts == gold_lines

        def build(
            kind: str, gold_index: int, out_index: int, alt: str
        ) -> dict[str, object] | None:
            line_index, offset = positions[gold_index]
            gold_char = gold_ns[gold_index]
            if not aligned_lines:
                return None
            box, source = locate(
                gold_lines[line_index],
                offset,
                textlines[line_index][1],
                chars,
                gold_char,
            )
            return {
                "instance_id": f"{token}#{gold_index}",
                "case_id": case_id,
                "run_id": output["run_id"],
                "output_fingerprint": output["output_fingerprint"],
                "kind": kind,
                "corpus": page["corpus"],
                "stem": page["stem"],
                "image": page["image"],
                "gold_index": gold_index,
                "out_index": out_index,
                "gold_char": gold_char,
                "output_char": alt if kind == "disagreement" else gold_char,
                "alternative": alt,
                "box": list(box),
                "box_source": source,
            }

        for block in Levenshtein.opcodes(gold_ns, out_ns):
            if block.tag == "replace":
                span = min(
                    block.src_end - block.src_start, block.dest_end - block.dest_start
                )
                for k in range(span):
                    g, o = gold_ns[block.src_start + k], out_ns[block.dest_start + k]
                    if not (is_cjk(g) and is_cjk(o)):
                        skipped_noncjk += 1
                        continue
                    row = build(
                        "disagreement", block.src_start + k, block.dest_start + k, o
                    )
                    if row is not None:
                        instances.append(row)
                        pair_counts[(g, o)] += 1
                        pair_counts[(o, g)] += 1
            elif block.tag == "equal":
                for k in range(block.src_end - block.src_start):
                    g = gold_ns[block.src_start + k]
                    if is_cjk(g):
                        controls_pool.append(
                            {
                                "case_id": case_id,
                                "gold_index": block.src_start + k,
                                "out_index": block.dest_start + k,
                            }
                        )

    counterpart = {}
    for (g, o), count in pair_counts.items():
        if counterpart.get(g) is None or pair_counts[(g, counterpart[g])] < count:
            counterpart[g] = o
    eligible = []
    for item in controls_pool:
        case_id = str(item["case_id"])
        page = pages[case_id]
        token = case_id.removesuffix("-transcribe-development").removeprefix("mthv2-")
        eligible.append((token, item))
    rng = random.Random(361004)
    controls: list[dict[str, object]] = []
    outputs_gold: dict[
        str, tuple[str, list[tuple[int, int]], list[str], object, object]
    ] = {}
    for token, item in sorted(
        eligible, key=lambda pair: (pair[0], pair[1]["gold_index"])
    ):
        case_id = str(item["case_id"])
        if case_id not in outputs_gold:
            page = pages[case_id]
            gold_matches = [
                p for p in GOLD_ROOT.glob("*.json") if p.stem.lower() == token.lower()
            ]
            gold = str(json.loads(gold_matches[0].read_text(encoding="utf-8"))["text"])
            gold_ns, positions = gold_line_map(gold)
            gold_lines = [no_space(line) for line in gold.split("\n")]
            label_dir = TEST_ROOT / "assets" / str(page["corpus"])
            textlines = parse_label_textline(
                label_dir / "label_textline" / f"{page['stem']}.txt"
            )
            chars = parse_label_char(label_dir / "label_char" / f"{page['stem']}.txt")
            if [no_space(text) for text, _ in textlines] != gold_lines:
                outputs_gold[case_id] = ("", [], [], None, None)
            else:
                outputs_gold[case_id] = (
                    gold_ns,
                    positions,
                    gold_lines,
                    textlines,
                    chars,
                )
        gold_ns, positions, gold_lines, textlines, chars = outputs_gold[case_id]
        if not gold_ns:
            continue
        gold_index = int(item["gold_index"])
        g = gold_ns[gold_index]
        alt = counterpart.get(g)
        if alt is None or alt == g:
            continue
        page = pages[case_id]
        line_index, offset = positions[gold_index]
        box, source = locate(
            gold_lines[line_index], offset, textlines[line_index][1], chars, g
        )
        controls.append(
            {
                "instance_id": f"{token}#{gold_index}",
                "case_id": case_id,
                "run_id": outputs[case_id]["run_id"],
                "output_fingerprint": outputs[case_id]["output_fingerprint"],
                "kind": "control",
                "corpus": page["corpus"],
                "stem": page["stem"],
                "image": page["image"],
                "gold_index": gold_index,
                "out_index": int(item["out_index"]),
                "gold_char": g,
                "output_char": g,
                "alternative": alt,
                "box": list(box),
                "box_source": source,
            }
        )
    rng.shuffle(controls)
    controls = controls[: args.controls]

    rows = instances + controls
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        "".join(
            json.dumps(r, ensure_ascii=False, separators=(",", ":")) + "\n"
            for r in rows
        ),
        encoding="utf-8",
        newline="\n",
    )
    sources = Counter(r["box_source"] for r in rows)
    print(
        f"collected {len(instances)} disagreements + {len(controls)} controls "
        f"(pool {len(controls_pool)}), skipped non-CJK {skipped_noncjk}, "
        f"boxes {dict(sources)} -> {args.output}"
    )


def crop_image(row: dict[str, object]) -> bytes:
    image_path = REPOSITORY_ROOT / str(row["image"])
    image = cv2.imread(str(image_path), cv2.IMREAD_COLOR)
    if image is None:
        raise RuntimeError(f"cannot read {image_path}")
    height, width = image.shape[:2]
    x1, y1, x2, y2 = (float(v) for v in row["box"])
    pad_x, pad_y = 0.3 * (x2 - x1) + 4, 0.3 * (y2 - y1) + 4
    x1, x2 = max(0, int(x1 - pad_x)), min(width, int(x2 + pad_x))
    y1, y2 = max(0, int(y1 - pad_y)), min(height, int(y2 + pad_y))
    crop = image[y1:y2, x1:x2]
    if crop.size == 0:
        raise RuntimeError(f"empty crop for {row['instance_id']}")
    long_side = max(crop.shape[:2])
    if long_side < 256:
        scale = 256 / long_side
        crop = cv2.resize(
            crop,
            (
                max(1, round(crop.shape[1] * scale)),
                max(1, round(crop.shape[0] * scale)),
            ),
            interpolation=cv2.INTER_CUBIC,
        )
    ok, encoded = cv2.imencode(".png", crop)
    if not ok:
        raise RuntimeError(f"encode failed for {row['instance_id']}")
    return encoded.tobytes()


def cmd_adjudicate(args: argparse.Namespace) -> None:
    rows = [
        json.loads(line)
        for line in args.manifest.read_text(encoding="utf-8").splitlines()
    ]
    done: set[str] = set()
    spent = 0.0
    if args.output.exists():
        for line in args.output.read_text(encoding="utf-8").splitlines():
            record = json.loads(line)
            done.add(record["instance_id"])
            spent += float(record.get("cost_usd") or 0.0)
    prompt_sha = hashlib.sha256(PROMPT.encode("utf-8")).hexdigest()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    pending = [r for r in rows if r["instance_id"] not in done]
    for index, row in enumerate(pending, 1):
        if spent >= args.max_cost:
            raise SystemExit(
                f"budget stop: observed spend {spent:.6f} USD >= {args.max_cost} USD"
            )
        seed = int(
            hashlib.sha256(f"ab#{row['instance_id']}".encode("utf-8")).hexdigest()[:8],
            16,
        )
        gold_is_a = seed % 2 == 0
        form_a = row["gold_char"] if gold_is_a else row["alternative"]
        form_b = row["alternative"] if gold_is_a else row["gold_char"]
        started = perf_counter()
        try:
            crop = crop_image(row)
            value, response = generate_json(
                ModelRequest(
                    model=ADJUDICATOR_MODEL,
                    prompt=PROMPT.format(form_a=form_a, form_b=form_b),
                    system=(
                        "You are a paleography assistant deciding which exact "
                        "glyph form is drawn in one character crop."
                    ),
                    images=(ImageContent(data=crop, mime="image/png"),),
                    temperature=0.0,
                    max_output_tokens=512,
                    media_resolution="high",
                    json_output=True,
                    json_schema=SCHEMA,
                    thinking_level="minimal",
                )
            )
        except (GatewayError, RuntimeError) as error:
            value, response = (
                {"written_form": None, "reasoning": f"adjudication failed: {error}"},
                None,
            )
        chosen = None
        if value["written_form"] == "A":
            chosen = form_a
        elif value["written_form"] == "B":
            chosen = form_b
        record = {
            "instance_id": row["instance_id"],
            "case_id": row["case_id"],
            "kind": row["kind"],
            "gold_char": row["gold_char"],
            "output_char": row["output_char"],
            "alternative": row["alternative"],
            "box_source": row["box_source"],
            "gold_is_a": gold_is_a,
            "adjudicator_model": ADJUDICATOR_MODEL,
            "prompt_sha256": prompt_sha,
            "written_form": value["written_form"],
            "chosen_char": chosen,
            "reasoning": value["reasoning"],
            "failed": response is None,
            "cost_usd": None if response is None else response.cost_usd,
            "latency_seconds": perf_counter() - started,
        }
        with args.output.open("a", encoding="utf-8", newline="\n") as target:
            target.write(json.dumps(record, ensure_ascii=False, separators=(",", ":")))
            target.write("\n")
        spent += float(record["cost_usd"] or 0.0)
        print(
            f"{index}/{len(pending)} {row['instance_id']} [{row['kind']}]: "
            f"{value['written_form']} chosen={chosen} gold={row['gold_char']} "
            f"{record['cost_usd'] or 0.0:.6f} USD",
            flush=True,
        )


def cmd_score(args: argparse.Namespace) -> None:
    manifest = {
        r["instance_id"]: r
        for r in map(json.loads, args.manifest.read_text(encoding="utf-8").splitlines())
    }
    results = [
        json.loads(line)
        for line in args.results.read_text(encoding="utf-8").splitlines()
    ]
    tallies: Counter[str] = Counter()
    fixes_by_case: dict[str, list[tuple[int, str]]] = {}
    for record in results:
        row = manifest[record["instance_id"]]
        kind = record["kind"]
        if record["failed"] or record["written_form"] in (None, "neither", "illegible"):
            tallies[f"{kind}_unresolved"] += 1
            continue
        chosen = record["chosen_char"]
        if kind == "disagreement":
            if chosen == record["gold_char"]:
                tallies["disagreement_fixed"] += 1
                fixes_by_case.setdefault(str(row["case_id"]), []).append(
                    (int(row["out_index"]), str(record["gold_char"]))
                )
            else:
                tallies["disagreement_kept_output"] += 1
        else:
            if chosen == record["gold_char"]:
                tallies["control_kept"] += 1
            else:
                tallies["control_broken"] += 1
    outputs = champion_outputs(args.runs, args.fingerprint)
    per_page = []
    for case_id, output in sorted(outputs.items()):
        token = case_id.removesuffix("-transcribe-development").removeprefix("mthv2-")
        gold_matches = [
            p for p in GOLD_ROOT.glob("*.json") if p.stem.lower() == token.lower()
        ]
        gold_ns = no_space(
            str(json.loads(gold_matches[0].read_text(encoding="utf-8"))["text"])
        )
        out_ns = list(no_space(str(output["combined"])))
        before = sum(
            b.src_end - b.src_start
            for b in Levenshtein.opcodes(gold_ns, "".join(out_ns))
            if b.tag == "equal"
        ) / max(len(gold_ns), 1)
        for out_index, gold_char in fixes_by_case.get(case_id, []):
            out_ns[out_index] = gold_char
        after = sum(
            b.src_end - b.src_start
            for b in Levenshtein.opcodes(gold_ns, "".join(out_ns))
            if b.tag == "equal"
        ) / max(len(gold_ns), 1)
        per_page.append(
            {
                "case_id": case_id,
                "recall_before": round(before, 6),
                "recall_after": round(after, 6),
                "fixes_applied": len(fixes_by_case.get(case_id, [])),
            }
        )
    resolved_disagreements = (
        tallies["disagreement_fixed"] + tallies["disagreement_kept_output"]
    )
    resolved_controls = tallies["control_kept"] + tallies["control_broken"]
    cost = sum(float(r.get("cost_usd") or 0.0) for r in results)
    summary = {
        "tallies": dict(tallies),
        "fix_rate_among_resolved": (
            tallies["disagreement_fixed"] / resolved_disagreements
            if resolved_disagreements
            else None
        ),
        "break_rate_among_resolved": (
            tallies["control_broken"] / resolved_controls if resolved_controls else None
        ),
        "recall_before_mean": round(
            sum(p["recall_before"] for p in per_page) / len(per_page), 6
        ),
        "recall_after_mean": round(
            sum(p["recall_after"] for p in per_page) / len(per_page), 6
        ),
        "adjudication_cost_usd": round(cost, 6),
        "per_page": per_page,
    }
    args.output.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )
    print(json.dumps({k: v for k, v in summary.items() if k != "per_page"}, indent=2))
    for page in per_page:
        print(
            f"  {page['case_id'].removeprefix('mthv2-').removesuffix('-transcribe-development')}: "
            f"{page['recall_before']:.4f} -> {page['recall_after']:.4f} "
            f"(+{page['fixes_applied']} fixes)"
        )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    collect = sub.add_parser("collect")
    collect.add_argument("--runs", nargs="+", required=True)
    collect.add_argument("--fingerprint", required=True)
    collect.add_argument("--controls", type=int, default=200)
    collect.add_argument("--output", type=Path, required=True)
    collect.set_defaults(handler=cmd_collect)
    adjudicate = sub.add_parser("adjudicate")
    adjudicate.add_argument("--manifest", type=Path, required=True)
    adjudicate.add_argument("--output", type=Path, required=True)
    adjudicate.add_argument("--max-cost", type=float, required=True)
    adjudicate.set_defaults(handler=cmd_adjudicate)
    score = sub.add_parser("score")
    score.add_argument("--manifest", type=Path, required=True)
    score.add_argument("--results", type=Path, required=True)
    score.add_argument("--runs", nargs="+", required=True)
    score.add_argument("--fingerprint", required=True)
    score.add_argument("--output", type=Path, required=True)
    score.set_defaults(handler=cmd_score)
    args = parser.parse_args(sys.argv[1:] if argv is None else argv)
    args.handler(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
