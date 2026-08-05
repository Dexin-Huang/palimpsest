from __future__ import annotations

import hashlib
import json
import zipfile
from pathlib import Path

import pytest
import yaml

from palimpsest.cli import build_parser
from palimpsest.factory import cli, rig
from palimpsest.factory.evaluation.candidate import (
    canonical_json,
    content_fingerprint,
    load_candidate,
)

_CANDIDATE = Path(
    "palimpsest/factory/candidates/transcribe/"
    "zh-luna-toolbelt8-regions-development-v1.yaml"
)
_READ_CANDIDATE = Path(
    "palimpsest/factory/candidates/read/zh-current-production-moving-v1.yaml"
)
_RUNTIME = {
    "python": {"implementation": "cpython", "version": "3.14.0"},
    "packages": {"palimpsest-cli": "0.2.0"},
    "executors": {"omp": "omp/test"},
}


def _fixed_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(rig, "_runtime_manifest", lambda _sources: _RUNTIME)


def _rewrite_zip(
    source: Path,
    target: Path,
    *,
    replace: dict[str, bytes] | None = None,
    extra: dict[str, bytes] | None = None,
) -> None:
    replacements = replace or {}
    additions = extra or {}
    with zipfile.ZipFile(source, "r") as current:
        members = {
            info.filename: replacements.get(info.filename, current.read(info.filename))
            for info in current.infolist()
        }
    members.update(additions)
    with zipfile.ZipFile(target, "w", compression=zipfile.ZIP_DEFLATED) as changed:
        for name, content in members.items():
            changed.writestr(name, content)


def test_rig_export_is_deterministic_and_import_is_idempotent(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fixed_runtime(monkeypatch)
    first_path = tmp_path / "first.palrig"
    second_path = tmp_path / "second.palrig"

    first = rig.export_rig(_CANDIDATE, first_path)
    second = rig.export_rig(_CANDIDATE, second_path)

    expected = load_candidate(_CANDIDATE)
    assert first.rig_fingerprint == second.rig_fingerprint
    assert first.archive_sha256 == second.archive_sha256
    assert first_path.read_bytes() == second_path.read_bytes()
    assert first.candidate.fingerprint == expected.fingerprint
    assert first.candidate.tracked is False
    assert first.candidate.can_auto_qualify is False
    with pytest.raises(FileExistsError):
        rig.export_rig(_CANDIDATE, first_path)
    assert first_path.read_bytes() == second_path.read_bytes()

    store_root = tmp_path / "store"
    imported = rig.import_rig(
        first_path, store_root, expected_archive_sha256=first.archive_sha256
    )
    repeated = rig.import_rig(
        first_path, store_root, expected_archive_sha256=first.archive_sha256
    )

    assert imported.archive_path == repeated.archive_path
    assert imported.store_path == store_root.resolve() / first.rig_fingerprint
    assert imported.archive_path.name == "rig.palrig"
    assert (imported.store_path / "candidate.yaml").is_file()
    assert (imported.store_path / "prompt.txt").is_file()
    assert rig.load_rig_candidate(imported.archive_path).tracked is False
    benchmark_candidate = cli._load_candidate_reference(imported.archive_path)
    assert benchmark_candidate.fingerprint == expected.fingerprint
    assert benchmark_candidate.tracked is False

    with pytest.raises(rig.RigError, match="archive SHA-256 mismatch"):
        rig.import_rig(
            first_path,
            tmp_path / "rejected",
            expected_archive_sha256="0" * 64,
        )
    assert not (tmp_path / "rejected").exists()


def test_rig_manifest_names_the_complete_agent_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fixed_runtime(monkeypatch)
    bundle = rig.export_rig(_CANDIDATE, tmp_path / "rig.palrig")

    with zipfile.ZipFile(bundle.archive_path, "r") as archive:
        manifest = json.loads(archive.read("manifest.json"))
        names = set(archive.namelist())

    candidate = manifest["candidate"]
    assert manifest["record_kind"] == "palimpsest-agent-rig"
    assert manifest["rig_fingerprint"] == bundle.rig_fingerprint
    assert candidate["model"] == "openai-codex/gpt-5.6-luna"
    assert candidate["model_identity"] == "fixed"
    assert candidate["prompt_name"] == "transcribe/zh/toolbelt3"
    assert candidate["options"]["extension_source"].startswith("import type")
    assert manifest["runtime"] == _RUNTIME
    assert "candidate.yaml" in names
    assert "prompt.txt" in names
    assert any(name.startswith("implementation/palimpsest/") for name in names)


def test_rig_rejects_changed_or_unsafe_archive_members(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fixed_runtime(monkeypatch)
    source = rig.export_rig(_CANDIDATE, tmp_path / "source.palrig").archive_path

    changed = tmp_path / "changed.palrig"
    _rewrite_zip(source, changed, replace={"prompt.txt": b"changed"})
    with pytest.raises(rig.RigError, match="member (hash|size) mismatch"):
        rig.verify_rig(changed)

    unsafe = tmp_path / "unsafe.palrig"
    _rewrite_zip(source, unsafe, extra={"../escape.py": b"pass\n"})
    with pytest.raises(rig.RigError, match="Unsafe rig member path"):
        rig.verify_rig(unsafe)
    assert not (tmp_path / "escape.py").exists()


def test_rig_rejects_a_self_consistent_foreign_source_closure(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fixed_runtime(monkeypatch)
    source = rig.export_rig(_CANDIDATE, tmp_path / "source.palrig").archive_path
    foreign = tmp_path / "foreign.palrig"

    with zipfile.ZipFile(source, "r") as archive:
        members = {name: archive.read(name) for name in archive.namelist()}
    manifest = json.loads(members["manifest.json"])
    record = next(
        item for item in manifest["files"] if item["role"] == "implementation"
    )
    path = record["path"]
    members[path] += b"\n# foreign source\n"
    record["size"] = len(members[path])
    record["sha256"] = hashlib.sha256(members[path]).hexdigest()
    payload = dict(manifest)
    payload.pop("rig_fingerprint")
    manifest["rig_fingerprint"] = content_fingerprint(payload)
    members["manifest.json"] = (canonical_json(manifest) + "\n").encode("utf-8")
    with zipfile.ZipFile(foreign, "w", compression=zipfile.ZIP_DEFLATED) as archive:
        for name, content in members.items():
            archive.writestr(name, content)

    with pytest.raises(rig.RigError, match="implementation source differs"):
        rig.verify_rig(foreign)


def test_rig_rejects_runtime_drift_and_moving_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _fixed_runtime(monkeypatch)
    archive = rig.export_rig(_CANDIDATE, tmp_path / "rig.palrig").archive_path
    monkeypatch.setattr(
        rig,
        "_runtime_manifest",
        lambda _sources: {**_RUNTIME, "executors": {"omp": "omp/changed"}},
    )

    with pytest.raises(rig.RigError, match="runtime differs"):
        rig.verify_rig(archive)

    moving_record = yaml.safe_load(_READ_CANDIDATE.read_text(encoding="utf-8"))
    moving_record["id"] = "read/rig-test-moving-model"
    moving_record["model"] = "token-plan/qwen3.8-max-latest"
    moving_path = tmp_path / "moving.yaml"
    moving_path.write_text(
        yaml.safe_dump(moving_record, sort_keys=False), encoding="utf-8"
    )
    with pytest.raises(rig.RigError, match="fixed model identity"):
        rig.export_rig(moving_path, tmp_path / "moving.palrig")


def test_rig_cli_routes_export_and_import() -> None:
    export = build_parser().parse_args(
        [
            "rig",
            "export",
            "--candidate",
            "candidate.yaml",
            "--output",
            "agent.palrig",
        ]
    )
    imported = build_parser().parse_args(
        [
            "rig",
            "import",
            "agent.palrig",
            "--expected-sha256",
            "a" * 64,
            "--store-root",
            "rigs",
        ]
    )

    assert export.func is cli.cmd_rig_export
    assert export.candidate == Path("candidate.yaml")
    assert export.output == Path("agent.palrig")
    assert imported.func is cli.cmd_rig_import
    assert imported.bundle == Path("agent.palrig")
    assert imported.store_root == Path("rigs")
    assert imported.expected_sha256 == "a" * 64


@pytest.mark.parametrize(
    "argv",
    [
        ["rig", "export", "--candidate", "candidate.yaml"],
        ["rig", "import"],
        ["rig", "import", "agent.palrig"],
    ],
)
def test_rig_cli_rejects_incomplete_commands(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as raised:
        build_parser().parse_args(argv)
    assert raised.value.code == 2


def testresolve_subprocess_executable_ignores_shims(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    shim_dir = tmp_path / "shims"
    binary_dir = tmp_path / "bin"
    shim_dir.mkdir()
    binary_dir.mkdir()
    (shim_dir / "omp.cmd").write_bytes(b"@echo shim")
    (binary_dir / "omp.exe").write_bytes(b"real binary")
    monkeypatch.setenv("PATH", f"{shim_dir}{rig.os.pathsep}{binary_dir}")

    resolved = rig.resolve_subprocess_executable("omp")
    if rig.os.name == "nt":
        # The .cmd shim in the earlier PATH entry must not win.
        assert resolved == binary_dir / "omp.exe"
    else:
        assert resolved is None or resolved.name == "omp"


@pytest.mark.skipif(
    rig.resolve_subprocess_executable("omp") is None,
    reason="OMP executor is not installed",
)
def testomp_executor_pin_binds_bytes() -> None:
    pin = rig.omp_executor_pin()
    assert set(pin) == {"version", "executable_sha256"}
    assert pin["version"].startswith("omp/")
    assert len(pin["executable_sha256"]) == 64
    # Deterministic across invocations on an unchanged installation.
    assert rig.omp_executor_pin() == pin
