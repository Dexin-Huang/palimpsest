from __future__ import annotations

import hashlib
import json
import shutil
import zipfile
from collections.abc import Mapping
from pathlib import Path

from setuptools.build_meta import build_wheel

from palimpsest.factory.evaluation.candidate import load_candidate
from palimpsest.factory.evaluation.judge import load_judge
from palimpsest.factory.evaluation.metrics import MetricRegistry
from palimpsest.factory.evaluation.response_schemas import trusted_response_schemas
from palimpsest.factory.evaluation.station_metrics.read import register_read_metrics
from palimpsest.factory.evaluation.suite import (
    CaseAsset,
    load_suite,
    validate_candidate_suite,
)


PROJECT_ROOT = Path(__file__).resolve().parents[1]
FACTORY_ROOT = PROJECT_ROOT / "palimpsest" / "factory"
EVALUATION_ROOT = FACTORY_ROOT / "evaluation"
SUITE_PATH = (
    EVALUATION_ROOT
    / "suites"
    / "read"
    / "zh-vatican-borg-cin-361-f004r-development-v1.yaml"
)
CANDIDATE_ROOT = FACTORY_ROOT / "candidates" / "read"
CANDIDATE_PATHS = (
    CANDIDATE_ROOT / "zh-vatican-f004r-low-thinking-development-v1.yaml",
    CANDIDATE_ROOT / "zh-vatican-f004r-high-thinking-development-v1.yaml",
    CANDIDATE_ROOT / "zh-current-production-moving-v1.yaml",
)
SOURCE_MANIFEST = "https://digi.vatlib.it/iiif/MSS_Borg.cin.361/manifest.json"
SOURCE_IMAGE_SHA256 = "244f6465336cc9af64fa18164efdf2053de99b2709eaed02f23bf61e78ab48fa"
RIGHTS = "Images Copyright Biblioteca Apostolica Vaticana"
JUDGE_PATH = FACTORY_ROOT / "judges" / "read-image-pairwise-gemini-3.6-v1.yaml"


def _assets(value: CaseAsset | Mapping[str, CaseAsset]):
    if isinstance(value, Mapping):
        yield from value.values()
    else:
        yield value


def test_tracked_chinese_read_development_resources_load_and_ship(
    tmp_path: Path, monkeypatch
) -> None:
    metrics = MetricRegistry()
    register_read_metrics(metrics)
    judge = load_judge(
        JUDGE_PATH,
        response_schema_resolver=trusted_response_schemas(),
    )
    suite = load_suite(
        SUITE_PATH,
        metric_resolver=metrics,
        probe_resolver={},
        judge_resolver={judge.id: judge},
        verify_local=True,
    )
    candidates = tuple(load_candidate(path) for path in CANDIDATE_PATHS)
    for candidate in candidates:
        validate_candidate_suite(candidate, suite)

    assert suite.id == "read/zh-vatican-borg-cin-361-f004r-development/v1"
    assert suite.station == "read"
    assert suite.qualification_eligible is False
    assert suite.can_auto_qualify is False
    assert "development-only" in suite.mission.lower()
    assert "non-qualifying" in suite.mission.lower()
    assert "not independent double transcription" in suite.mission.lower()
    assert suite.promotion.minimum_completed_cases == 1
    assert suite.slice_policy.minimum_cases == 1
    assert {metric.name for metric in suite.primary_metrics} <= {
        metric.name for metric in metrics.all()
    }

    assert len(suite.cases) == 1
    case = suite.cases[0]
    assert case.doc_id == "vatican_borg_cin_361"
    assert case.page_id == "f004r"
    assert len(case.pages) == 536
    assert [page["order"] for page in case.pages] == list(range(1, 537))
    target_page = next(page for page in case.pages if page["page_id"] == case.page_id)
    assert target_page == {
        "filename": "f004r.jpg",
        "height": 1815,
        "label": "4",
        "order": 4,
        "page_id": "f004r",
        "url": "https://digi.vatlib.it/iiifimage/MSS_Borg.cin.361/Borg.cin.361_0004.jp2/full/max/0/default.jpg",
        "width": 2388,
    }

    source_image = case.inputs["page_image_clean"]
    assert isinstance(source_image, CaseAsset)
    assert source_image.path is None
    assert source_image.source == f"iiif:{target_page['url']}"
    assert source_image.sha256 == SOURCE_IMAGE_SHA256
    assert len(source_image.sha256) == 64
    assert case.license == f"{RIGHTS}; source manifest {SOURCE_MANIFEST}"
    assert case.adjudication["method"] == "user_corrected_transcription"
    assert "double" not in str(case.adjudication["method"])
    assert "user_corrected_development" in case.strata

    input_assets = [asset for value in case.inputs.values() for asset in _assets(value)]
    reference_assets = list(case.references.values())
    for asset in (*input_assets, *reference_assets):
        if asset.path is None:
            assert asset.source is not None
            assert len(asset.sha256) == 64
            continue
        tracked = EVALUATION_ROOT / asset.path
        assert tracked.is_file()
        assert hashlib.sha256(tracked.read_bytes()).hexdigest() == asset.sha256

    input_identity = json.dumps(
        {
            name: {
                "path": asset.path,
                "source": asset.source,
                "sha256": asset.sha256,
            }
            for name, asset in case.inputs.items()
            if isinstance(asset, CaseAsset)
        },
        sort_keys=True,
    )
    assert set(case.inputs) == {"page_image_clean", "page_regions"}
    assert "gold/" not in input_identity
    assert case.references["transcription"].path not in input_identity
    gold_path = EVALUATION_ROOT / case.references["transcription"].path
    gold = json.loads(gold_path.read_text(encoding="utf-8"))
    assert gold["adjudication"]["qualification_status"] == "development_non_qualifying"
    assert gold["adjudication"]["method"] == "user_corrected_transcription"
    assert gold["text"] not in input_identity

    assert len(suite.judges) == 1
    assert suite.judges[0].metric == "blind_image_pairwise"
    assert suite.judges[0].judge == judge
    assert judge.response_schema == "pairwise_preference/v1"

    low, high, current = candidates
    for candidate in candidates:
        assert candidate.station == "read"
        assert candidate.variant == "default"
        assert candidate.grain == "page"
        assert candidate.consumes == ("page_image_clean", "page_regions")
        assert candidate.optional_consumes == ()
        assert candidate.produces == "page_transcription"
        assert candidate.prompt_name == "read/zh/diplomatic"
        assert candidate.prompt_hash is not None
    for candidate in (low, high):
        assert candidate.model == "gemini-3.6-flash"
        assert candidate.model_identity == "fixed"
        assert candidate.can_auto_qualify
    assert {key for key in low.params if low.params[key] != high.params[key]} == {
        "thinking_level"
    }
    assert set(low.params) == set(high.params)
    assert low.params["thinking_level"] == "low"
    assert high.params["thinking_level"] == "high"
    assert current.id == "read/zh-current-production-moving-v1"
    assert current.model == "gemini-flash-latest"
    assert current.model_identity == "moving"
    assert current.can_auto_qualify is False
    assert current.params == high.params

    wheel_source = tmp_path / "wheel-source"
    shutil.copytree(PROJECT_ROOT / "palimpsest", wheel_source / "palimpsest")
    shutil.copy2(PROJECT_ROOT / "pyproject.toml", wheel_source / "pyproject.toml")
    shutil.copy2(PROJECT_ROOT / "README.md", wheel_source / "README.md")
    wheel_dir = tmp_path / "wheel"
    monkeypatch.chdir(wheel_source)
    wheel_path = wheel_dir / build_wheel(str(wheel_dir))
    with zipfile.ZipFile(wheel_path) as archive:
        wheel_names = set(archive.namelist())

    resource_suffixes = {
        ".json",
        ".jsonl",
        ".yaml",
        ".yml",
        ".txt",
        ".png",
        ".jpg",
        ".jpeg",
        ".epub",
        ".html",
        ".xml",
    }
    copied_project_root = wheel_source
    copied_factory_root = copied_project_root / "palimpsest" / "factory"
    copied_evaluation_root = copied_factory_root / "evaluation"
    resource_roots = (
        copied_factory_root / "candidates",
        copied_factory_root / "judges",
        copied_factory_root / "prompts",
        copied_evaluation_root / "suites",
        copied_evaluation_root / "cases",
        copied_evaluation_root / "gold",
    )
    tracked_resources = {
        path.relative_to(copied_project_root).as_posix()
        for root in resource_roots
        if root.exists()
        for path in root.rglob("*")
        if path.is_file() and path.suffix in resource_suffixes
    }
    assert tracked_resources <= wheel_names
    assert {
        "palimpsest/factory/candidates/read/zh-vatican-f004r-low-thinking-development-v1.yaml",
        "palimpsest/factory/candidates/read/zh-current-production-moving-v1.yaml",
        "palimpsest/factory/judges/read-image-pairwise-gemini-3.6-v1.yaml",
        "palimpsest/factory/prompts/judge/read/image-pairwise-v1.txt",
        "palimpsest/factory/evaluation/suites/read/zh-vatican-borg-cin-361-f004r-development-v1.yaml",
        "palimpsest/factory/evaluation/cases/read/zh-vatican-borg-cin-361-f004r-development-v1.jsonl",
        "palimpsest/factory/evaluation/cases/read/vatican_borg_cin_361/f004r.regions.json",
        "palimpsest/factory/evaluation/gold/read/vatican_borg_cin_361/f004r.user-corrected.json",
    } <= wheel_names
