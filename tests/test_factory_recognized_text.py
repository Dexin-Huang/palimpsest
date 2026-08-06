from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from pathlib import Path

import pytest

from palimpsest.factory.evaluation.candidate import canonical_json
from palimpsest.factory.evaluation.metrics import MetricRegistry
from palimpsest.factory.evaluation.station_metrics.read import register_read_metrics
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


PROJECT_ROOT = Path(__file__).resolve().parents[1]
EVALUATION_ROOT = PROJECT_ROOT / "palimpsest" / "factory" / "evaluation"
PAGE_ID = "0001-001-26-26"
DIPLOMATIC_TEXT_SHA256 = (
    "4aa4876f33202cf076fe37aff35cbae915c19de26ce9bc570a9b3895cb710ecd"
)
RECOGNIZED_TEXT_SHA256 = (
    "4d0f5056726b4094e3ad3de09529c54eb1432ad44ce2e2173fbed65fe80351b0"
)
RECOGNIZED_PROVENANCE_SHA256 = (
    "7b8682965ab31563856cde25ba463c01a0f6e8719e0414ddc34b4ebe8beb4b27"
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


def test_recognized_metrics_score_full_page_commentary_exactly_once() -> None:
    registry = MetricRegistry()
    register_read_metrics(registry)
    gold = _recognized_gold("廿六\n註", "廿六\n註")
    flat_output = {"transcription": "二十 六", "commentary": "註"}
    layered_output = {
        "layers": [
            {"kind": "primary", "text": "二十 六"},
            {"kind": "commentary", "text": "註"},
        ],
        "commentary": "this top-level duplicate must not be appended",
    }

    for metric_name in (
        "recognized_text_v1_partial_gold_character_error_rate",
    ):
        assert registry.observe(metric_name, flat_output, gold) == 0.0
        assert registry.observe(metric_name, layered_output, gold) == 0.0
        assert registry.observe(metric_name, {"commentary": "註"}, gold) is None
        assert registry.observe(metric_name, flat_output, {"text": "legacy"}) is None

    assert (
        registry.observe(
            "recognized_text_v1_partial_gold_character_error_rate",
            {"transcription": "二十 六", "commentary": []},
            gold,
        )
        is None
    )


def test_adjudicated_mthv2_gold_and_manifests_remain_bound() -> None:
    records: list[dict[str, object]] = []
    resources = (
        ("mthv2-advisory-core", "mthv2-advisory-core-v1.jsonl"),
        ("mthv2-development", "mthv2-development-v1.jsonl"),
    )

    for gold_directory, manifest_name in resources:
        relative_gold_path = f"gold/transcribe/{gold_directory}/tkh-0001-001-26-26.json"
        gold_path = EVALUATION_ROOT / relative_gold_path
        gold_bytes = gold_path.read_bytes()
        record = json.loads(gold_bytes)
        records.append(record)

        assert gold_bytes == (canonical_json(record) + "\n").encode("utf-8")
        assert record["schema_version"] == 2
        assert record["recognized_profile"] == RECOGNIZED_TEXT_PROFILE_ID
        assert _text_sha256(record["text"]) == DIPLOMATIC_TEXT_SHA256
        assert _text_sha256(record["recognized_text"]) == RECOGNIZED_TEXT_SHA256
        assert recognized_reference_text(record) == record["recognized_text"]

        provenance = record["recognized_provenance"]
        assert provenance["canonical_payload_sha256"] == RECOGNIZED_PROVENANCE_SHA256
        assert provenance["input_sha256"] == DIPLOMATIC_TEXT_SHA256
        assert provenance["output_sha256"] == RECOGNIZED_TEXT_SHA256
        assert [
            (change["line_index"], change["line_offset"], change["from"], change["to"])
            for change in provenance["transformations"]
        ] == [(4, 5, "巳", "已"), (8, 3, "巳", "已"), (10, 1, "巳", "已")]

        manifest_path = EVALUATION_ROOT / "cases" / "transcribe" / manifest_name
        manifest_bytes = manifest_path.read_bytes()
        assert manifest_bytes.endswith(b"\n")
        cases = [json.loads(line) for line in manifest_bytes.splitlines()]
        matching_cases = [case for case in cases if case["page_id"] == PAGE_ID]
        assert len(matching_cases) == 1
        matching_case = matching_cases[0]
        assert (
            canonical_json(matching_case).encode("utf-8") in manifest_bytes.splitlines()
        )
        transcription = matching_case["references"]["transcription"]
        assert transcription["path"] == relative_gold_path
        assert transcription["sha256"] == hashlib.sha256(gold_bytes).hexdigest()

    assert records[0]["text"] == records[1]["text"]
    assert records[0]["recognized_text"] == records[1]["recognized_text"]
    assert records[0]["recognized_provenance"] == records[1]["recognized_provenance"]
