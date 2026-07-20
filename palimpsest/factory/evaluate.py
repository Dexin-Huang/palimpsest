"""Did the system improve? Compare two transcription sources, same pages.

Two layers, cheap first:

- **Deterministic metrics** (free): contamination by non-page content
  (digitization watermarks, copyright banners), repetition loops (a failure
  mode of overwhelmed reads), and text volume.
- **Blind pairwise judge** (paid, optional): a judge model sees the ORIGINAL
  page image plus both transcriptions, anonymized and order-randomized per
  page (seeded by page_id — reproducible), and picks the more faithful
  diplomatic transcription. Use a different model than the one that produced
  either side.

The harness reports; the operator (or the calling agent) renders the verdict
from the table.
"""

from __future__ import annotations

import hashlib
import json
from collections import Counter
from dataclasses import dataclass
from pathlib import Path

from palimpsest.factory.config import LIBRARY_ROOT
from palimpsest.factory.gateway import ModelRequest, generate_json
from palimpsest.factory.workspace.io import read_json
from palimpsest.factory.workspace.layout import artifact_path

# Vocabulary that can only come from the digitization, never from the page.
CONTAMINATION_TERMS = (
    "biblioteca apostolica",
    "all rights",
    "reserved",
    "copyright",
    "amlad",
    "ntt data",
    "vaticana ©",
    "©",
)

JUDGE_SCHEMA = {
    "type": "object",
    "properties": {
        "winner": {"type": "string", "enum": ["A", "B", "tie"]},
        "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
        "reasoning": {"type": "string"},
    },
    "required": ["winner", "confidence", "reasoning"],
}

JUDGE_PROMPT = """You are judging two diplomatic transcriptions of the SAME
manuscript page, shown in the attached image. Decide which transcription is
more faithful to what is actually written ON THE PAGE.

Criteria, in order of importance:
1. No invented content: text not visible on the page — including library
   watermarks, copyright banners, or text bleeding from a neighboring page —
   is a serious defect, not extra credit.
2. Completeness: all genuine page content (main text, marginalia, numerals)
   is transcribed.
3. Reading accuracy of the visible words and abbreviations.
4. No degenerate output (repeated lines, hallucinated filler).

=== TRANSCRIPTION A ===
{A}
=== END A ===

=== TRANSCRIPTION B ===
{B}
=== END B ===

Answer as JSON: winner ("A", "B", or "tie"), confidence, and one-paragraph
reasoning."""


@dataclass
class PageEval:
    page_id: str
    metrics: dict[str, dict[str, float]]  # side -> metric -> value
    judge_winner: str | None = None  # 'old' | 'new' | 'tie'
    judge_confidence: str | None = None
    judge_reasoning: str | None = None


def text_metrics(text: str) -> dict[str, float]:
    lower = text.lower()
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    counts = Counter(lines)
    repeated = sum(count for count in counts.values() if count > 2)
    return {
        "chars": len(text),
        "contamination_hits": sum(lower.count(term) for term in CONTAMINATION_TERMS),
        "repeated_line_fraction": round(repeated / len(lines), 3) if lines else 0.0,
    }


def load_reference_texts(jsonl_path: Path) -> dict[str, str]:
    texts = {}
    for line in jsonl_path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            record = json.loads(line)
            texts[record["page_id"]] = record.get("text", "")
    return texts


def evaluate(
    doc_id: str,
    reference_jsonl: Path,
    page_ids: list[str],
    *,
    library_root: Path = LIBRARY_ROOT,
    judge_model: str | None = None,
    image_doc_id: str | None = None,
) -> list[PageEval]:
    reference = load_reference_texts(reference_jsonl)
    results = []
    for page_id in page_ids:
        new_text = read_json(
            artifact_path(doc_id, "page_transcription", page_id, library_root)
        )["text"]
        old_text = reference.get(page_id, "")
        evaluation = PageEval(
            page_id=page_id,
            metrics={
                "old": text_metrics(old_text),
                "new": text_metrics(new_text),
            },
        )
        if judge_model:
            evaluation = _judge(
                evaluation,
                old_text,
                new_text,
                page_id,
                judge_model=judge_model,
                image_doc_id=image_doc_id or doc_id,
                library_root=library_root,
            )
        results.append(evaluation)
    return results


def _judge(
    evaluation: PageEval,
    old_text: str,
    new_text: str,
    page_id: str,
    *,
    judge_model: str,
    image_doc_id: str,
    library_root: Path,
) -> PageEval:
    image = _page_image(image_doc_id, page_id, library_root)
    # reproducible A/B assignment per page, decorrelated from side
    old_is_a = int(hashlib.sha256(page_id.encode()).hexdigest(), 16) % 2 == 0
    text_a, text_b = (old_text, new_text) if old_is_a else (new_text, old_text)

    verdict, _ = generate_json(
        ModelRequest(
            model=judge_model,
            prompt=JUDGE_PROMPT.replace("{A}", text_a or "(empty)").replace(
                "{B}", text_b or "(empty)"
            ),
            images=(image,),
            temperature=0.1,
            json_output=True,
            json_schema=JUDGE_SCHEMA,
        )
    )
    winner = verdict["winner"]
    if winner == "tie":
        evaluation.judge_winner = "tie"
    else:
        won_a = winner == "A"
        evaluation.judge_winner = "old" if won_a == old_is_a else "new"
    evaluation.judge_confidence = verdict["confidence"]
    evaluation.judge_reasoning = verdict["reasoning"]
    return evaluation


def _page_image(doc_id: str, page_id: str, library_root: Path) -> Path:
    image = artifact_path(doc_id, "page_image", page_id, library_root)
    if not image.exists():
        raise FileNotFoundError(f"No page_image for {doc_id}/{page_id}")
    return image


def _int_or_float(value: float) -> str:
    return str(int(value)) if float(value).is_integer() else f"{value:.3f}"


def render_table(results: list[PageEval]) -> str:
    lines = [
        f"{'page':>10} {'chars o/n':>14} {'contam o/n':>12} "
        f"{'rep o/n':>12} {'judge':>6} {'conf':>7}",
    ]
    for r in results:
        old, new = r.metrics["old"], r.metrics["new"]
        lines.append(
            f"{r.page_id:>10} "
            f"{_int_or_float(old['chars']) + '/' + _int_or_float(new['chars']):>14} "
            f"{_int_or_float(old['contamination_hits']) + '/' + _int_or_float(new['contamination_hits']):>12} "
            f"{_int_or_float(old['repeated_line_fraction']) + '/' + _int_or_float(new['repeated_line_fraction']):>12} "
            f"{r.judge_winner or '-':>6} {r.judge_confidence or '-':>7}"
        )
    if any(r.judge_winner for r in results):
        tally = Counter(r.judge_winner for r in results if r.judge_winner)
        lines.append(f"judge tally: {dict(tally)}")
    return "\n".join(lines)
