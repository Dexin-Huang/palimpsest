"""apparatus: machine checks around an emended reading.

Two instruments, both pure code:

- ``coverage_failures`` makes "no silent changes" a contract instead of a
  prompt plea: every span where the reading departs from the original must
  be covered by an anchored apparatus entry, and parallel citations must
  name work·section.
- ``systematic_sweeps`` catches the class no model caught in evaluation
  (P.3477: 候 fixed to 焦's reading in one line, left corrupt in the next):
  when the same graph substitution was applied in several places, every
  surviving instance of the old graph becomes a checklist item for the
  agent to adjudicate — treated or explained, never skipped silently.
"""

from __future__ import annotations

from collections import Counter
from difflib import SequenceMatcher

_ANCHOR_SLACK = 2  # snippet boundaries may sit a hair off the diff edge


def coverage_failures(sections: list[dict], emended: dict) -> list[str]:
    """Every deviation covered, every entry anchored, citations well-formed.
    ``sections`` carries the manuscript originals; ``emended`` is the
    artifact payload. Returns human-readable failures; empty means PASS."""
    failures: list[str] = []
    originals = {s["heading"]: s["original"] for s in sections}

    if [s["heading"] for s in emended.get("sections", [])] != list(originals):
        failures.append("section headings/order do not match the manuscript")

    entries: dict[str, list[dict]] = {}
    for entry in emended.get("apparatus", []):
        section = entry.get("section", "")
        entries.setdefault(section, []).append(entry)
        if section not in originals:
            failures.append(f"apparatus references unknown section: {section!r}")
        evidence = entry.get("evidence", "")
        if evidence.lstrip().casefold().startswith("parallel") and "·" not in evidence:
            failures.append(
                f"parallel evidence without work·section citation: {evidence!r}"
            )

    for section in emended.get("sections", []):
        heading = section["heading"]
        original = originals.get(heading, "")
        reading = section["reading"]

        # Flatten every apparatus entry into the candidate spans it anchors on
        # each side of the reading.  Coverage cares about spans, not which
        # entry supplied them.
        anchors: tuple[list[tuple[int, int]], list[tuple[int, int]]] = ([], [])
        for entry in entries.get(heading, []):
            for side, (label, text) in enumerate(
                (("original", original), ("emended", reading))
            ):
                snippet = entry.get(label, "")
                spans = _occurrences(text, snippet)
                anchors[side].extend(spans)
                if snippet and not spans:
                    failures.append(
                        f"[{heading}] apparatus {label} not found in "
                        f"{'text' if side == 0 else 'reading'}: {snippet[:30]!r}"
                    )

        for tag, i1, i2, j1, j2 in SequenceMatcher(
            None, original, reading
        ).get_opcodes():
            if tag == "equal":
                continue
            side, lo, hi = (0, i1, i2) if i2 > i1 else (1, j1, j2)
            if not any(
                start - _ANCHOR_SLACK <= lo and hi <= end + _ANCHOR_SLACK
                for start, end in anchors[side]
            ):
                failures.append(
                    f"[{heading}] UNCOVERED change: {original[i1:i2]!r} -> "
                    f"{reading[j1:j2]!r} (chars {i1}-{i2})"
                )
    return failures


def _occurrences(text: str, snippet: str) -> list[tuple[int, int]]:
    """All candidate anchors, including overlapping repeated readings."""
    if not snippet:
        return []
    spans = []
    start = 0
    while (index := text.find(snippet, start)) >= 0:
        spans.append((index, index + len(snippet)))
        start = index + 1
    return spans


def systematic_sweeps(
    sections: list[dict], emended: dict, min_instances: int = 2
) -> list[str]:
    """Single-graph substitutions applied ``min_instances``+ times whose old
    graph still survives in the reading — each survivor is a checklist line
    for the agent to treat or explain."""
    originals = {s["heading"]: s["original"] for s in sections}
    substitutions: Counter[tuple[str, str]] = Counter()
    for section in emended.get("sections", []):
        original = originals.get(section["heading"], "")
        for tag, i1, i2, j1, j2 in SequenceMatcher(
            None, original, section["reading"]
        ).get_opcodes():
            if tag == "replace" and i2 - i1 == 1 and j2 - j1 == 1:
                substitutions[(original[i1:i2], section["reading"][j1:j2])] += 1

    checklist = []
    full_reading = "\n".join(s["reading"] for s in emended.get("sections", []))
    for (old, new), count in substitutions.items():
        if count < min_instances:
            continue
        survivors = full_reading.count(old)
        if survivors:
            checklist.append(
                f"you emended {old!r} -> {new!r} in {count} places, but "
                f"{old!r} still appears {survivors}x in the reading — treat "
                f"each instance or record in the apparatus why it stands"
            )
    return checklist
