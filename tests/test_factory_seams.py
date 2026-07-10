"""Seam-overlap trimming against the real P.3477 seams that motivated it.

Gallica photographed the scroll as overlapping segments; the duplicate
columns were transcribed twice with divergent readings (temperature and
damage), so detection must be fuzzy — but positionally strict enough that
formulaic pulse-manual lines never trigger a false trim.
"""

from palimpsest.factory.seams import find_overlap, prev_page_id, trim_overlap

# gallica_pelliot_chinois_3477 seam page_0000 → page_0001: same physical
# columns, independently (and differently) transcribed.
P0_TAIL = (
    "關上三部而爲陽之脉陽脉常浮速尺中一部常沉而\n"
    "遲開前爲陽開後爲陰鼓則吐瀉數則下陽弦頭痛\n"
    "萇痛九候相應不得目失一使隻則病二夜則二度"
)
P1_HEAD = (
    "遲聞前爲易開後爲陰鼓則吐陰鼓則下陽弦頭痛\n"
    "腹痛九候相應不得相失一候後則病二候後\n"
    "則病甚三候後則危所謂後者應不俱捻脉拍下輕\n"
    "重脉名\n"
    "類形狀第二"
)

# Formulaic but genuinely distinct pulse definitions (page_0002 style):
# high phrase repetition, must NOT match.
FORMULAIC_PREV = "濡陰按之無有舉之有餘名日濡\n弱陰按之盡牽舉之無有名日弱"
FORMULAIC_NEXT = "遲陰按之盡牽舉之無有不前不去名日遲\n芤陰按之無有舉之来至兩旁名日芤"


def test_finds_real_seam_overlap():
    overlap = find_overlap(P0_TAIL, P1_HEAD)
    assert overlap is not None
    assert overlap["lines"] == 2


def test_trim_drops_only_the_rephotographed_columns():
    trimmed, report = trim_overlap(P0_TAIL, P1_HEAD)
    assert trimmed.startswith("則病甚三候後則危")
    assert "類形狀第二" in trimmed
    assert report["lines"] == 2
    assert report["dropped_text"].startswith("遲聞前爲易")


def test_formulaic_lines_do_not_false_positive():
    assert find_overlap(FORMULAIC_PREV, FORMULAIC_NEXT) is None
    text, report = trim_overlap(FORMULAIC_PREV, FORMULAIC_NEXT)
    assert text == FORMULAIC_NEXT
    assert report is None


def test_single_line_match_is_never_enough():
    # a lone similar line is indistinguishable from formulaic text; the
    # fail-safe is to keep it (duplicated seam, never lost text)
    prev = "some earlier column\n玄感脉經一卷"
    cur = "玄感脉經一卷\nnext column entirely different"
    assert find_overlap(prev, cur) is None


def test_blank_and_first_pages_are_safe():
    assert find_overlap("", "any text") is None
    assert find_overlap("any text", "") is None
    pages = ({"page_id": "page_0000"}, {"page_id": "page_0001"})
    assert prev_page_id(pages, "page_0000") is None
    assert prev_page_id(pages, "page_0001") == "page_0000"
