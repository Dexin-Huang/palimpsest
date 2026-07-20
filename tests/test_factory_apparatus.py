"""Apparatus checks against the failure modes observed in the P.3477
executor evaluation: silent orthographic normalization (caught on terra),
unanchored entries, missing citations, and the systematic-substitution
sweep that no model performed reliably (候/焦)."""

from palimpsest.factory.apparatus import coverage_failures, systematic_sweeps

SECTIONS = [{"heading": "one", "original": "甲乙丙丁\n戊己庚辛"}]


def _artifact(reading, apparatus):
    return {
        "sections": [{"heading": "one", "reading": reading}],
        "apparatus": apparatus,
    }


def test_clean_pass():
    art = _artifact("甲乙丙丁\n戊己庚辛", [])
    assert coverage_failures(SECTIONS, art) == []
    assert systematic_sweeps(SECTIONS, art) == []


def test_covered_change_passes():
    art = _artifact(
        "甲乙丙戌\n戊己庚辛",
        [
            {
                "section": "one",
                "original": "丙丁",
                "emended": "丙戌",
                "reason": "x",
                "evidence": "ink",
            }
        ],
    )
    assert coverage_failures(SECTIONS, art) == []


def test_silent_change_rejected():
    art = _artifact("甲乙丙戌\n戊己庚辛", [])
    failures = coverage_failures(SECTIONS, art)
    assert len(failures) == 1 and "UNCOVERED" in failures[0]


def test_unanchored_entry_rejected():
    art = _artifact(
        "甲乙丙丁\n戊己庚辛",
        [
            {
                "section": "one",
                "original": "不在文中",
                "emended": "甲乙",
                "reason": "x",
                "evidence": "ink",
            }
        ],
    )
    assert any("not found in text" in f for f in coverage_failures(SECTIONS, art))


def test_parallel_without_citation_rejected():
    art = _artifact(
        "甲乙丙丁\n戊己庚辛",
        [
            {
                "section": "one",
                "original": "甲",
                "emended": "甲",
                "reason": "x",
                "evidence": "parallel: somewhere",
            }
        ],
    )
    assert any("work·section" in f for f in coverage_failures(SECTIONS, art))


def test_heading_mismatch_rejected():
    art = {"sections": [{"heading": "other", "reading": ""}], "apparatus": []}
    assert any("headings" in f for f in coverage_failures(SECTIONS, art))


def test_sweep_flags_surviving_instances():
    # 焦 emended to 候 twice, but one 焦 left standing — the P.3477 miss
    sections = [{"heading": "one", "original": "三焦者九焦之中有焦"}]
    art = _artifact(
        "三候者九候之中有焦",
        [
            {
                "section": "one",
                "original": "三焦",
                "emended": "三候",
                "reason": "x",
                "evidence": "structure",
            },
            {
                "section": "one",
                "original": "九焦",
                "emended": "九候",
                "reason": "x",
                "evidence": "structure",
            },
        ],
    )
    sweeps = systematic_sweeps(sections, art)
    assert len(sweeps) == 1 and "'焦'" in sweeps[0] and "1x" in sweeps[0]


def test_sweep_quiet_when_all_treated():
    sections = [{"heading": "one", "original": "三焦者九焦"}]
    art = _artifact("三候者九候", [])
    assert systematic_sweeps(sections, art) == []
