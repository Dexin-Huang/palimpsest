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
        entries.setdefault(entry.get("section", ""), []).append(entry)
        evidence = entry.get("evidence", "")
        if evidence.startswith("parallel") and "·" not in evidence:
            failures.append(
                f"parallel evidence without work·section citation: {evidence!r}")

    for section in emended.get("sections", []):
        heading = section["heading"]
        original = originals.get(heading, "")
        reading = section["reading"]

        anchored = []
        for entry in entries.get(heading, []):
            o_at = original.find(entry["original"]) if entry.get("original") else -1
            r_at = reading.find(entry["emended"]) if entry.get("emended") else -1
            if entry.get("original") and o_at < 0:
                failures.append(
                    f"[{heading}] apparatus original not found in text: "
                    f"{entry['original'][:30]!r}")
            if entry.get("emended") and r_at < 0:
                failures.append(
                    f"[{heading}] apparatus emended not found in reading: "
                    f"{entry['emended'][:30]!r}")
            anchored.append((
                o_at, o_at + len(entry.get("original", "")) if o_at >= 0 else -1,
                r_at, r_at + len(entry.get("emended", "")) if r_at >= 0 else -1,
            ))

        def covered(side: int, lo: int, hi: int) -> bool:
            return any(
                a[side] >= 0 and a[side] - _ANCHOR_SLACK <= lo
                and hi <= a[side + 1] + _ANCHOR_SLACK
                for a in anchored)

        for tag, i1, i2, j1, j2 in SequenceMatcher(
                None, original, reading).get_opcodes():
            if tag == "equal":
                continue
            if not (covered(0, i1, i2) if i2 > i1 else covered(2, j1, j2)):
                failures.append(
                    f"[{heading}] UNCOVERED change: {original[i1:i2]!r} -> "
                    f"{reading[j1:j2]!r} (chars {i1}-{i2})")
    return failures


def systematic_sweeps(sections: list[dict], emended: dict,
                      min_instances: int = 2) -> list[str]:
    """Single-graph substitutions applied ``min_instances``+ times whose old
    graph still survives in the reading — each survivor is a checklist line
    for the agent to treat or explain."""
    originals = {s["heading"]: s["original"] for s in sections}
    substitutions: Counter[tuple[str, str]] = Counter()
    for section in emended.get("sections", []):
        original = originals.get(section["heading"], "")
        for tag, i1, i2, j1, j2 in SequenceMatcher(
                None, original, section["reading"]).get_opcodes():
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
                f"each instance or record in the apparatus why it stands")
    return checklist
