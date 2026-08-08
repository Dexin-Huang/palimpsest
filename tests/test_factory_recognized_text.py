from __future__ import annotations

import hashlib
from collections.abc import Mapping, Sequence

import pytest

from palimpsest.factory.recognized_text import (
    RECOGNIZED_TEXT_PROFILE_ID,
    RECOGNIZED_TEXT_PROFILE_SHA256,
    RECOGNIZED_TEXT_PROFILE_VERSION,
    RECOGNIZED_TEXT_V2_PROFILE_ID,
    RECOGNIZED_TEXT_V2_PROFILE_SHA256,
    RECOGNIZED_TEXT_V2_PROFILE_VERSION,
    normalize_recognized_text_v1,
    normalize_recognized_text_v2,
    recognized_provenance_sha256,
    recognized_reference_text,
)


def _text_sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _recognized_gold(
    text: str,
    recognized_text: str,
    transformations: Sequence[Mapping[str, object]] = (),
) -> dict[str, object]:
    authority: dict[str, object] = {
        "method": "synthetic_test_adjudication",
        "source": "urn:palimpsest:test",
        "version": 1,
    }
    transformation_records = [
        dict(transformation) for transformation in transformations
    ]
    input_sha256 = _text_sha256(text)
    output_sha256 = _text_sha256(recognized_text)
    provenance = {
        "authority": authority,
        "transformations": transformation_records,
        "input_sha256": input_sha256,
        "output_sha256": output_sha256,
        "canonical_payload_sha256": recognized_provenance_sha256(
            profile=RECOGNIZED_TEXT_PROFILE_ID,
            authority=authority,
            transformations=transformation_records,
            input_sha256=input_sha256,
            output_sha256=output_sha256,
        ),
    }
    return {
        "schema_version": 2,
        "text": text,
        "recognized_profile": RECOGNIZED_TEXT_PROFILE_ID,
        "recognized_text": recognized_text,
        "recognized_provenance": provenance,
    }


def test_recognized_v1_normalizes_only_declared_equivalences() -> None:
    source = "廿 六\r\n卅 三\r卌 四\n巳 已\nA\u0304\n甲 乙\t丙"
    expected = "二十六\n三十三\n四十四\n巳已\nĀ\n甲乙\t丙"

    assert RECOGNIZED_TEXT_PROFILE_ID == "recognized_text_v1"
    assert RECOGNIZED_TEXT_PROFILE_VERSION == 1
    assert len(RECOGNIZED_TEXT_PROFILE_SHA256) == 64
    assert normalize_recognized_text_v1(source) == expected
    assert normalize_recognized_text_v1(expected) == expected
    assert normalize_recognized_text_v1("日曰已巳真靜異護") == "日曰已巳真靜異護"
    assert normalize_recognized_text_v1("巳") != normalize_recognized_text_v1("已")
    with pytest.raises(TypeError, match="text must be a string"):
        normalize_recognized_text_v1(None)  # type: ignore[arg-type]


def test_recognized_v2_extends_v1_without_mutating_it() -> None:
    assert RECOGNIZED_TEXT_V2_PROFILE_ID == "recognized_text_v2"
    assert RECOGNIZED_TEXT_V2_PROFILE_VERSION == 2
    assert len(RECOGNIZED_TEXT_V2_PROFILE_SHA256) == 64
    assert RECOGNIZED_TEXT_V2_PROFILE_SHA256 != RECOGNIZED_TEXT_PROFILE_SHA256

    # Audited v2 folds: attested same-word variant forms unify.
    assert normalize_recognized_text_v2("无湏逺䖏歳") == "無須遠處歲"
    # v1 stays frozen: the same forms pass through untouched.
    assert normalize_recognized_text_v1("无湏逺䖏歳") == "无湏逺䖏歳"
    # Semantically distinct lookalikes and sign-off-queue pairs never fold.
    assert normalize_recognized_text_v2("日曰已巳着著閒間") == "日曰已巳着著閒間"
    # v1 classes carry into v2 unchanged.
    assert normalize_recognized_text_v2("佛陁眞丗衆經目録") == "佛陀真世眾經目錄"


def test_recognized_reference_requires_complete_valid_provenance() -> None:
    transformation = {
        "line_index": 0,
        "line_offset": 2,
        "from": "巳",
        "to": "已",
        "reason": "The verb is completed in context.",
    }
    gold = _recognized_gold("受花巳還散", "受花已還散", (transformation,))

    assert recognized_reference_text(gold) == "受花已還散"
    assert recognized_reference_text({"schema_version": 1, "text": "legacy"}) is None

    with pytest.raises(ValueError, match="recognized fields are partial"):
        recognized_reference_text(
            {
                "schema_version": 2,
                "text": "受花巳還散",
                "recognized_text": "受花已還散",
            }
        )

    wrong_profile = dict(gold)
    wrong_profile["recognized_profile"] = "recognized_text_v2"
    with pytest.raises(ValueError, match="recognized_profile"):
        recognized_reference_text(wrong_profile)

    wrong_payload = dict(gold)
    wrong_payload_provenance = dict(gold["recognized_provenance"])  # type: ignore[arg-type]
    wrong_payload_provenance["canonical_payload_sha256"] = "0" * 64
    wrong_payload["recognized_provenance"] = wrong_payload_provenance
    with pytest.raises(ValueError, match="canonical_payload_sha256"):
        recognized_reference_text(wrong_payload)

    wrong_coordinate = dict(transformation)
    wrong_coordinate["line_offset"] = 1
    with pytest.raises(ValueError, match="does not match text at its coordinates"):
        recognized_reference_text(
            _recognized_gold("受花巳還散", "受花已還散", (wrong_coordinate,))
        )
