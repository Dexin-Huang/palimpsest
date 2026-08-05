"""Conservative, versioned equivalence classes for premodern Han glyph forms.

The diplomatic transcription remains unchanged.  This module provides a derived
semantic/comparison view only.  Classes intentionally exclude visually similar
but semantically distinct confusions such as 日/曰, 已/巳, 真/靜, and 異/護.
"""

from __future__ import annotations

import hashlib
import json
import unicodedata

HAN_VARIANT_TABLE_VERSION = 1

# The first character is the display representative used by normalized semantic
# views.  Every member is a single Unicode scalar and may occur in only one class.
HAN_VARIANT_CLASSES_V1: tuple[tuple[str, ...], ...] = (
    ("會", "㑹"),
    ("最", "㝡"),
    ("哉", "㢤"),
    ("留", "㽞"),
    ("莊", "㽵"),
    ("鐵", "䥫"),
    ("萬", "万"),
    ("世", "丗"),
    ("久", "乆"),
    ("乘", "乗"),
    ("來", "来"),
    ("減", "减"),
    ("別", "别"),
    ("勅", "勑"),
    ("號", "号"),
    ("歎", "嘆"),
    ("嘗", "甞"),
    ("圓", "圎"),
    ("土", "圡"),
    ("增", "増"),
    ("寶", "寳"),
    ("爾", "尒"),
    ("屬", "属"),
    ("庾", "𢈔"),
    ("往", "徃"),
    ("從", "従"),
    ("念", "𫝹"),
    ("惡", "𢙣"),
    ("惱", "𢙉"),
    ("教", "敎"),
    ("明", "眀"),
    ("曾", "曽"),
    ("查", "査"),
    ("歲", "𡻕"),
    ("為", "爲"),
    ("蓋", "盖"),
    ("眾", "衆"),
    ("禮", "礼"),
    ("緣", "縁"),
    ("總", "緫"),
    ("舍", "舎"),
    ("蘊", "藴"),
    ("處", "𠁅"),
    ("讚", "讃"),
    ("踴", "踊"),
    ("遊", "逰"),
    ("達", "逹"),
    ("那", "𨚗"),
    ("釋", "𥼶"),
    ("陀", "陁"),
    ("青", "靑"),
    ("面", "靣"),
    ("高", "髙"),
    ("真", "眞"),
    ("錄", "録"),
)

# Version-two additions come from the 2026-08-03 fold audit of R_train residual
# pairs (exodia research/palimpsest-transcription/fold-audit-v2.json).  Every
# addition is externally attested: a Unihan variants-file relation, a Unihan
# kDefinition link, or an identical kDefinition gloss.  Pairs with independent-
# word collisions (着/著, 閒/間) and pairs without external evidence stay out.
_V2_CLASS_EXTENSIONS: dict[str, tuple[str, ...]] = {
    "處": ("䖏",),
    "歲": ("歳",),
}
_V2_NEW_CLASSES: tuple[tuple[str, ...], ...] = (
    ("須", "湏"),
    ("無", "无"),
    ("臥", "卧"),
    ("飲", "飮"),
    ("災", "灾"),
    ("內", "内"),
    ("彌", "弥"),
    ("決", "决"),
    ("棄", "弃"),
    ("場", "塲"),
    ("珍", "珎"),
    ("虛", "虚"),
    ("隨", "随"),
    ("辭", "辞"),
    ("劫", "刧"),
    ("侶", "侣"),
    ("或", "㦯"),
    ("功", "㓛"),
    ("遠", "逺"),
    ("藏", "蔵"),
    ("勳", "勲"),
    ("宜", "冝"),
)
HAN_VARIANT_CLASSES_V2: tuple[tuple[str, ...], ...] = (
    tuple(
        equivalence_class + _V2_CLASS_EXTENSIONS.get(equivalence_class[0], ())
        for equivalence_class in HAN_VARIANT_CLASSES_V1
    )
    + _V2_NEW_CLASSES
)
HAN_VARIANT_TABLE_V2_VERSION = 2


def _build_translation(classes: tuple[tuple[str, ...], ...]) -> dict[int, str]:
    translation: dict[int, str] = {}
    for equivalence_class in classes:
        if len(equivalence_class) < 2:
            raise RuntimeError("Han variant classes must contain at least two forms")
        representative = equivalence_class[0]
        for character in equivalence_class:
            if len(character) != 1:
                raise RuntimeError("Han variant forms must be one Unicode scalar")
            codepoint = ord(character)
            if codepoint in translation:
                raise RuntimeError(f"duplicate Han variant form: {character}")
            translation[codepoint] = representative
    return translation


def _table_sha256(classes: tuple[tuple[str, ...], ...]) -> str:
    return hashlib.sha256(
        json.dumps(classes, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    ).hexdigest()


_TRANSLATION_V1 = _build_translation(HAN_VARIANT_CLASSES_V1)
_TRANSLATION_V2 = _build_translation(HAN_VARIANT_CLASSES_V2)
HAN_VARIANT_TABLE_SHA256 = _table_sha256(HAN_VARIANT_CLASSES_V1)
HAN_VARIANT_TABLE_V2_SHA256 = _table_sha256(HAN_VARIANT_CLASSES_V2)


def normalize_han_variants_v1(text: str) -> str:
    """Return an NFC semantic view with only allowlisted glyph forms unified."""

    if not isinstance(text, str):
        raise TypeError("Han variant input must be a string")
    return unicodedata.normalize("NFC", text).translate(_TRANSLATION_V1)


def normalize_han_variants_v2(text: str) -> str:
    """Return the version-two NFC semantic view; v1 stays frozen for replay."""

    if not isinstance(text, str):
        raise TypeError("Han variant input must be a string")
    return unicodedata.normalize("NFC", text).translate(_TRANSLATION_V2)
