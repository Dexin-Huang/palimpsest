"""seams: deterministic trimming of re-photographed columns at page joins.

Some sources digitize a continuous scroll as overlapping segments (Gallica's
Dunhuang scrolls repeat ~2 columns at every cut), so adjacent pages carry two
independent transcriptions of the same physical ink. Left untrimmed, every
downstream artifact — translation, manuscript, EPUB — repeats the seam text,
usually with contradictory readings.

Matching is fuzzy because the duplicate columns were read by separate model
calls that can disagree on hard characters, and it demands at least TWO
aligned lines: single-line similarity cannot separate a divergent duplicate
read (P.3477 seam pair: 0.63) from two genuinely different lines sharing a
formula skeleton (pulse definitions: 0.73) — a 2-line joint match can
(true seams ≥0.72 avg, formulaic neighbors 0.48). When the two reads split
columns differently no alignment is found and the page is left untouched —
the failure mode is a duplicated seam, never lost text.
"""

from __future__ import annotations

from difflib import SequenceMatcher

MIN_OVERLAP_LINES = 2  # matches the source: Gallica repeats ~2 columns/cut
MAX_OVERLAP_LINES = 8
_AVG_SIMILARITY = 0.6
_MIN_SIMILARITY = 0.5


def find_overlap(prev_text: str, text: str) -> dict | None:
    """Longest k where this page's first k lines re-transcribe the previous
    page's last k lines. Returns ``{"lines": k, "similarity": avg}`` or None.
    """
    prev_lines = _content_lines(prev_text)
    lines = _content_lines(text)
    best = None
    top = min(len(prev_lines), len(lines), MAX_OVERLAP_LINES)
    for k in range(MIN_OVERLAP_LINES, top + 1):
        ratios = [
            SequenceMatcher(None, a, b).ratio()
            for a, b in zip(prev_lines[-k:], lines[:k])
        ]
        similarity = sum(ratios) / k
        if similarity >= _AVG_SIMILARITY and min(ratios) >= _MIN_SIMILARITY:
            best = {"lines": k, "similarity": round(similarity, 3)}
    return best


def trim_overlap(prev_text: str, text: str) -> tuple[str, dict | None]:
    """Drop this page's opening lines that duplicate the previous page's
    closing lines. Returns ``(text, report)``; report carries the dropped
    text so every trim stays auditable in the artifact."""
    overlap = find_overlap(prev_text, text)
    if overlap is None:
        return text, None
    lines = text.splitlines()
    content_seen = 0
    split_at = 0
    for split_at, line in enumerate(lines, start=1):
        content_seen += bool(line.strip())
        if content_seen == overlap["lines"]:
            break
    dropped = lines[:split_at]
    kept = lines[split_at:]
    return "\n".join(kept).strip("\n"), {
        **overlap,
        "dropped_text": "\n".join(dropped),
    }


def prev_page_id(pages: tuple[dict, ...], page_id: str) -> str | None:
    """The page whose tail this page's head is trimmed against."""
    for index, page in enumerate(pages):
        if page["page_id"] == page_id:
            return pages[index - 1]["page_id"] if index else None
    raise ValueError(f"page_id not found in ordered pages: {page_id}")


def _content_lines(text: str) -> list[str]:
    return [line.strip() for line in text.splitlines() if line.strip()]
