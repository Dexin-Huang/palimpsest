"""Portable, content-addressed agent-rig bundles.

A rig freezes one model-backed candidate, its skill prompt, the registered
station implementation source closure, and the local runtime versions that can
change harness behavior. Import validates compatibility with the installed
Palimpsest runtime; it never executes or installs bundled source code.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import zipfile
from collections.abc import Mapping
from dataclasses import dataclass
from importlib import metadata
from pathlib import Path, PurePosixPath
from typing import Any

import yaml

from palimpsest.factory import prompt_store
from palimpsest.factory.core import registry
from palimpsest.factory.evaluation.candidate import (
    ResolvedCandidate,
    canonical_json,
    content_fingerprint,
    load_candidate,
)

_SCHEMA_VERSION = 1
_RECORD_KIND = "palimpsest-agent-rig"
_ARCHIVE_SUFFIX = ".palrig"
_MANIFEST_PATH = "manifest.json"
_CANDIDATE_PATH = "candidate.yaml"
_PROMPT_PATH = "prompt.txt"
_STORED_ARCHIVE_NAME = "rig.palrig"
_PACKAGE_ROOT = Path(__file__).resolve().parents[1]
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")
_REQUIREMENT_NAME = re.compile(r"([A-Za-z0-9][A-Za-z0-9._-]*)")
_MAX_ARCHIVE_FILES = 512
_MAX_MEMBER_BYTES = 16 * 1024 * 1024
_MAX_ARCHIVE_BYTES = 64 * 1024 * 1024
_MAX_ARCHIVE_FILE_BYTES = 64 * 1024 * 1024
_ZIP_TIMESTAMP = (1980, 1, 1, 0, 0, 0)
_ALLOWED_ROLES = frozenset({"candidate", "skill_prompt", "implementation"})


class RigError(ValueError):
    """A rig bundle is malformed, incompatible, or unsafe to import."""


@dataclass(frozen=True, slots=True)
class RigBundle:
    archive_path: Path
    archive_sha256: str
    rig_fingerprint: str
    candidate: ResolvedCandidate
    store_path: Path | None = None


def export_rig(candidate_path: Path, output_path: Path) -> RigBundle:
    """Resolve one fixed-model candidate and write a deterministic rig archive."""
    candidate_path = Path(candidate_path)
    output_path = Path(output_path)
    _require_archive_suffix(output_path)
    if output_path.exists():
        raise FileExistsError(f"Rig archive already exists: {output_path}")

    candidate = load_candidate(candidate_path)
    _require_agent_candidate(candidate)
    prompt = prompt_store.load(candidate.prompt_name)
    station = registry.get(candidate.station, candidate.variant)
    source_paths = tuple(station.production_source_paths)

    files: dict[str, bytes] = {
        _CANDIDATE_PATH: _candidate_yaml(candidate),
        _PROMPT_PATH: prompt.text.encode("utf-8"),
    }
    roles = {
        _CANDIDATE_PATH: "candidate",
        _PROMPT_PATH: "skill_prompt",
    }
    for source in source_paths:
        try:
            relative = source.resolve().relative_to(_PACKAGE_ROOT).as_posix()
        except ValueError:
            raise RigError(
                f"Implementation source is outside the installed package: {source}"
            ) from None
        archive_name = f"implementation/palimpsest/{relative}"
        if archive_name in files:
            raise RigError(f"Duplicate implementation source path: {archive_name}")
        files[archive_name] = source.read_bytes()
        roles[archive_name] = "implementation"

    file_records = [
        _file_record(path, roles[path], content)
        for path, content in sorted(files.items())
    ]
    payload = {
        "schema_version": _SCHEMA_VERSION,
        "record_kind": _RECORD_KIND,
        "candidate": _candidate_manifest(candidate),
        "runtime": _runtime_manifest(source_paths),
        "files": file_records,
    }
    manifest = {
        **payload,
        "rig_fingerprint": content_fingerprint(payload),
    }
    manifest_bytes = (canonical_json(manifest) + "\n").encode("utf-8")

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(
        prefix=f".{output_path.name}.",
        suffix=".staged",
        dir=output_path.parent,
        delete=False,
    ) as handle:
        temporary = Path(handle.name)
    try:
        _write_archive(temporary, manifest_bytes, files)
        os.link(temporary, output_path)
        temporary.unlink()
    except BaseException:
        temporary.unlink(missing_ok=True)
        raise

    return verify_rig(output_path)


def verify_rig(archive_path: Path) -> RigBundle:
    """Verify archive integrity and exact local execution compatibility."""
    archive_path = _validated_archive_path(archive_path)
    archive_sha256 = _file_sha256(archive_path)
    bundle, _manifest, _files = _load_verified_rig(archive_path, archive_sha256)
    return bundle


def _load_verified_rig(
    archive_path: Path, archive_sha256: str
) -> tuple[RigBundle, dict[str, Any], dict[str, bytes]]:
    manifest, files = _read_archive(archive_path)
    candidate = _verify_local_compatibility(manifest, files)
    bundle = RigBundle(
        archive_path=archive_path.resolve(),
        archive_sha256=archive_sha256,
        rig_fingerprint=str(manifest["rig_fingerprint"]),
        candidate=candidate,
    )
    return bundle, manifest, files


def import_rig(
    archive_path: Path,
    store_root: Path,
    *,
    expected_archive_sha256: str,
) -> RigBundle:
    """Authenticate, verify, and atomically install a rig."""
    archive_path = _validated_archive_path(archive_path)
    expected_archive_sha256 = _require_sha256(
        expected_archive_sha256, "expected_archive_sha256"
    )
    archive_sha256 = _file_sha256(archive_path)
    if archive_sha256 != expected_archive_sha256:
        raise RigError(
            f"Rig archive SHA-256 mismatch: expected {expected_archive_sha256}, "
            f"got {archive_sha256}"
        )
    verified, manifest, files = _load_verified_rig(archive_path, archive_sha256)
    store_root = Path(store_root)
    store_root.mkdir(parents=True, exist_ok=True)
    destination = store_root / verified.rig_fingerprint

    if destination.exists():
        return _verified_store(destination, verified.rig_fingerprint)

    staged = Path(
        tempfile.mkdtemp(
            prefix=f".{verified.rig_fingerprint}.",
            suffix=".staged",
            dir=store_root,
        )
    )
    try:
        for member, content in {
            _MANIFEST_PATH: (canonical_json(manifest) + "\n").encode("utf-8"),
            **files,
        }.items():
            target = staged.joinpath(*PurePosixPath(member).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(content)
        shutil.copyfile(archive_path, staged / _STORED_ARCHIVE_NAME)
        try:
            staged.replace(destination)
        except FileExistsError:
            shutil.rmtree(staged)
            return _verified_store(destination, verified.rig_fingerprint)
    except BaseException:
        shutil.rmtree(staged, ignore_errors=True)
        raise

    return RigBundle(
        archive_path=(destination / _STORED_ARCHIVE_NAME).resolve(),
        archive_sha256=verified.archive_sha256,
        rig_fingerprint=verified.rig_fingerprint,
        candidate=verified.candidate,
        store_path=destination.resolve(),
    )


def load_rig_candidate(archive_path: Path) -> ResolvedCandidate:
    """Resolve an imported rig as an untracked evaluation candidate."""
    return verify_rig(archive_path).candidate


def _require_agent_candidate(candidate: ResolvedCandidate) -> None:
    if candidate.model is None or candidate.prompt_name is None:
        raise RigError("Agent rigs require a model-backed candidate")
    if candidate.model_identity != "fixed":
        raise RigError(
            f"Agent rigs require a fixed model identity, got {candidate.model!r}"
        )


def _candidate_yaml(candidate: ResolvedCandidate) -> bytes:
    record: dict[str, object] = {
        "schema_version": candidate.schema_version,
        "id": candidate.id,
        "station": candidate.station,
        "variant": candidate.variant,
        "model": candidate.model,
        "prompt": candidate.prompt_name,
        "params": _plain_json(candidate.params),
        "options": _plain_json(candidate.options),
    }
    if candidate.notes is not None:
        record["notes"] = candidate.notes
    rendered = yaml.safe_dump(
        record,
        allow_unicode=True,
        default_flow_style=False,
        sort_keys=False,
        width=4096,
    )
    return rendered.encode("utf-8")


def _candidate_manifest(candidate: ResolvedCandidate) -> dict[str, object]:
    return {
        "schema_version": candidate.schema_version,
        "id": candidate.id,
        "station": candidate.station,
        "variant": candidate.variant,
        "grain": candidate.grain,
        "consumes": list(candidate.consumes),
        "optional_consumes": list(candidate.optional_consumes),
        "produces": candidate.produces,
        "model": candidate.model,
        "model_identity": candidate.model_identity,
        "prompt_name": candidate.prompt_name,
        "prompt_hash": candidate.prompt_hash,
        "params": _plain_json(candidate.params),
        "options": _plain_json(candidate.options),
        "notes": candidate.notes,
        "implementation_fingerprint": candidate.implementation_fingerprint,
        "fingerprint": candidate.fingerprint,
    }


def _plain_json(value: object) -> object:
    return json.loads(canonical_json(value))


def _file_record(path: str, role: str, content: bytes) -> dict[str, object]:
    return {
        "path": path,
        "role": role,
        "size": len(content),
        "sha256": hashlib.sha256(content).hexdigest(),
    }


def _runtime_manifest(source_paths: tuple[Path, ...]) -> dict[str, object]:
    packages: dict[str, str] = {}
    try:
        requirements = metadata.requires("palimpsest-cli") or ()
        names = {"palimpsest-cli"}
        for requirement in requirements:
            if "extra ==" in requirement:
                continue
            match = _REQUIREMENT_NAME.match(requirement)
            if match:
                names.add(match.group(1))
        for name in sorted(names, key=str.casefold):
            packages[name] = metadata.version(name)
    except metadata.PackageNotFoundError as error:
        raise RigError(f"Cannot resolve rig runtime package: {error}") from error

    executors: dict[str, str] = {}
    relative_sources = {
        path.resolve().relative_to(_PACKAGE_ROOT).as_posix() for path in source_paths
    }
    if "factory/agent_cell.py" in relative_sources:
        try:
            completed = subprocess.run(
                ["omp", "--version"],
                check=True,
                capture_output=True,
                text=True,
                timeout=10,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise RigError(f"Cannot resolve OMP executor version: {error}") from error
        version = completed.stdout.strip() or completed.stderr.strip()
        if not version:
            raise RigError("OMP executor returned an empty version")
        executors["omp"] = version

    return {
        "python": {
            "implementation": sys.implementation.name,
            "version": ".".join(str(part) for part in sys.version_info[:3]),
        },
        "packages": packages,
        "executors": executors,
    }


def _write_archive(
    target: Path, manifest_bytes: bytes, files: Mapping[str, bytes]
) -> None:
    with zipfile.ZipFile(
        target,
        mode="w",
        compression=zipfile.ZIP_DEFLATED,
        compresslevel=9,
        strict_timestamps=True,
    ) as archive:
        for name, content in [(_MANIFEST_PATH, manifest_bytes), *sorted(files.items())]:
            info = zipfile.ZipInfo(name, date_time=_ZIP_TIMESTAMP)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.create_system = 3
            info.external_attr = (stat.S_IFREG | 0o644) << 16
            archive.writestr(
                info, content, compress_type=zipfile.ZIP_DEFLATED, compresslevel=9
            )


def _read_archive(archive_path: Path) -> tuple[dict[str, Any], dict[str, bytes]]:
    try:
        with zipfile.ZipFile(archive_path, mode="r") as archive:
            infos = archive.infolist()
            if not infos or len(infos) > _MAX_ARCHIVE_FILES:
                raise RigError(
                    f"Rig archive must contain 1-{_MAX_ARCHIVE_FILES} regular files"
                )
            names = [info.filename for info in infos]
            if len(names) != len(set(names)):
                raise RigError("Rig archive contains duplicate member names")

            total_size = 0
            for info in infos:
                _validate_member(info)
                total_size += info.file_size
            if total_size > _MAX_ARCHIVE_BYTES:
                raise RigError(f"Rig archive expands beyond {_MAX_ARCHIVE_BYTES} bytes")
            if _MANIFEST_PATH not in names:
                raise RigError("Rig archive has no manifest.json")

            manifest_bytes = archive.read(_MANIFEST_PATH)
            try:
                manifest = json.loads(manifest_bytes.decode("utf-8"))
            except (UnicodeDecodeError, json.JSONDecodeError) as error:
                raise RigError(
                    f"Rig manifest is not valid UTF-8 JSON: {error}"
                ) from error
            _validate_manifest(manifest)
            canonical = (canonical_json(manifest) + "\n").encode("utf-8")
            if manifest_bytes != canonical:
                raise RigError("Rig manifest is not canonical JSON")

            records = {str(record["path"]): record for record in manifest["files"]}
            expected_names = {_MANIFEST_PATH, *records}
            if set(names) != expected_names:
                extra = sorted(set(names) - expected_names)
                missing = sorted(expected_names - set(names))
                raise RigError(
                    f"Rig archive membership mismatch; extra={extra}, missing={missing}"
                )

            files: dict[str, bytes] = {}
            for path, record in records.items():
                content = archive.read(path)
                if len(content) != record["size"]:
                    raise RigError(f"Rig member size mismatch: {path}")
                if hashlib.sha256(content).hexdigest() != record["sha256"]:
                    raise RigError(f"Rig member hash mismatch: {path}")
                files[path] = content
            return manifest, files
    except (OSError, zipfile.BadZipFile, zipfile.LargeZipFile) as error:
        raise RigError(f"Cannot read rig archive {archive_path}: {error}") from error


def _validate_member(info: zipfile.ZipInfo) -> None:
    _safe_member_path(info.filename)
    if info.is_dir():
        raise RigError(f"Rig archive member must be a regular file: {info.filename}")
    if info.flag_bits & 0x1:
        raise RigError(f"Encrypted rig members are forbidden: {info.filename}")
    if info.compress_type not in {zipfile.ZIP_STORED, zipfile.ZIP_DEFLATED}:
        raise RigError(f"Unsupported rig compression for {info.filename}")
    if info.file_size > _MAX_MEMBER_BYTES:
        raise RigError(f"Rig member exceeds {_MAX_MEMBER_BYTES} bytes: {info.filename}")
    mode = info.external_attr >> 16
    file_type = stat.S_IFMT(mode)
    if file_type not in {0, stat.S_IFREG}:
        raise RigError(f"Non-regular rig member is forbidden: {info.filename}")


def _safe_member_path(value: object) -> str:
    if not isinstance(value, str) or not value or "\\" in value:
        raise RigError(f"Invalid rig member path: {value!r}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise RigError(f"Unsafe rig member path: {value!r}")
    return value


def _validate_manifest(value: object) -> None:
    if not isinstance(value, dict):
        raise RigError("Rig manifest must be a JSON object")
    expected = {
        "schema_version",
        "record_kind",
        "candidate",
        "runtime",
        "files",
        "rig_fingerprint",
    }
    if set(value) != expected:
        raise RigError(
            f"Rig manifest fields mismatch; expected {sorted(expected)}, got {sorted(value)}"
        )
    if value["schema_version"] != _SCHEMA_VERSION:
        raise RigError(f"Unsupported rig schema version: {value['schema_version']!r}")
    if value["record_kind"] != _RECORD_KIND:
        raise RigError(f"Unsupported rig record kind: {value['record_kind']!r}")
    _require_sha256(value["rig_fingerprint"], "rig_fingerprint")

    candidate = value["candidate"]
    candidate_fields = {
        "schema_version",
        "id",
        "station",
        "variant",
        "grain",
        "consumes",
        "optional_consumes",
        "produces",
        "model",
        "model_identity",
        "prompt_name",
        "prompt_hash",
        "params",
        "options",
        "notes",
        "implementation_fingerprint",
        "fingerprint",
    }
    if not isinstance(candidate, dict) or set(candidate) != candidate_fields:
        raise RigError("Rig candidate manifest has invalid fields")
    _require_sha256(candidate.get("fingerprint"), "candidate.fingerprint")
    _require_sha256(candidate.get("prompt_hash"), "candidate.prompt_hash")
    if candidate.get("model_identity") != "fixed" or not isinstance(
        candidate.get("model"), str
    ):
        raise RigError("Rig candidate must name a fixed model")
    if not isinstance(candidate.get("prompt_name"), str):
        raise RigError("Rig candidate must name a skill prompt")

    runtime = value["runtime"]
    if not isinstance(runtime, dict) or set(runtime) != {
        "python",
        "packages",
        "executors",
    }:
        raise RigError("Rig runtime manifest has invalid fields")

    records = value["files"]
    if not isinstance(records, list) or not records:
        raise RigError("Rig manifest files must be a non-empty list")
    paths: list[str] = []
    roles: list[str] = []
    for record in records:
        if not isinstance(record, dict) or set(record) != {
            "path",
            "role",
            "size",
            "sha256",
        }:
            raise RigError("Rig file record has invalid fields")
        path = _safe_member_path(record["path"])
        role = record["role"]
        if role not in _ALLOWED_ROLES:
            raise RigError(f"Invalid rig file role: {role!r}")
        size = record["size"]
        if type(size) is not int or size < 0 or size > _MAX_MEMBER_BYTES:
            raise RigError(f"Invalid rig file size for {path}: {size!r}")
        _require_sha256(record["sha256"], f"files[{path}].sha256")
        paths.append(path)
        roles.append(role)
    if paths != sorted(paths) or len(paths) != len(set(paths)):
        raise RigError("Rig file paths must be sorted and unique")
    if (
        paths.count(_CANDIDATE_PATH) != 1
        or roles[paths.index(_CANDIDATE_PATH)] != "candidate"
    ):
        raise RigError("Rig must contain candidate.yaml with the candidate role")
    if (
        paths.count(_PROMPT_PATH) != 1
        or roles[paths.index(_PROMPT_PATH)] != "skill_prompt"
    ):
        raise RigError("Rig must contain prompt.txt with the skill_prompt role")
    implementation_paths = [
        path
        for path, role in zip(paths, roles, strict=True)
        if role == "implementation"
    ]
    if not implementation_paths or any(
        not path.startswith("implementation/palimpsest/")
        for path in implementation_paths
    ):
        raise RigError("Rig implementation source closure is missing or malformed")

    payload = dict(value)
    supplied = payload.pop("rig_fingerprint")
    if content_fingerprint(payload) != supplied:
        raise RigError("Rig fingerprint does not match its canonical manifest")


def _verify_local_compatibility(
    manifest: Mapping[str, Any], files: Mapping[str, bytes]
) -> ResolvedCandidate:
    with tempfile.TemporaryDirectory(prefix="palimpsest-rig-") as temporary:
        candidate_path = Path(temporary) / _CANDIDATE_PATH
        candidate_path.write_bytes(files[_CANDIDATE_PATH])
        try:
            candidate = load_candidate(candidate_path, tracked=False)
        except Exception as error:
            raise RigError(
                f"Rig candidate is not executable in this installation: {error}"
            ) from error

    _require_agent_candidate(candidate)
    expected_candidate = _candidate_manifest(candidate)
    if expected_candidate != manifest["candidate"]:
        raise RigError("Rig candidate identity does not match the installed runtime")

    prompt = prompt_store.load(candidate.prompt_name)
    if prompt.text.encode("utf-8") != files[_PROMPT_PATH]:
        raise RigError(
            f"Installed prompt {candidate.prompt_name!r} differs from the rig skill prompt"
        )

    station = registry.get(candidate.station, candidate.variant)
    local_sources: dict[str, bytes] = {}
    for source in station.production_source_paths:
        try:
            relative = source.resolve().relative_to(_PACKAGE_ROOT).as_posix()
        except ValueError:
            raise RigError(
                f"Installed implementation source is outside the package: {source}"
            ) from None
        local_sources[f"implementation/palimpsest/{relative}"] = source.read_bytes()
    bundled_sources = {
        str(record["path"]): files[str(record["path"])]
        for record in manifest["files"]
        if record["role"] == "implementation"
    }
    if set(local_sources) != set(bundled_sources):
        raise RigError("Installed implementation source closure differs from the rig")
    for path, content in local_sources.items():
        if content != bundled_sources[path]:
            raise RigError(
                f"Installed implementation source differs from the rig: {path}"
            )

    runtime = _runtime_manifest(tuple(station.production_source_paths))
    if runtime != manifest["runtime"]:
        raise RigError("Installed runtime versions differ from the rig")
    return candidate


def _verified_store(destination: Path, expected_fingerprint: str) -> RigBundle:
    stored_archive = destination / _STORED_ARCHIVE_NAME
    verified = verify_rig(stored_archive)
    if verified.rig_fingerprint != expected_fingerprint:
        raise RigError(
            f"Rig store collision at {destination}: expected {expected_fingerprint}, "
            f"got {verified.rig_fingerprint}"
        )
    return RigBundle(
        archive_path=stored_archive.resolve(),
        archive_sha256=verified.archive_sha256,
        rig_fingerprint=verified.rig_fingerprint,
        candidate=verified.candidate,
        store_path=destination.resolve(),
    )


def _validated_archive_path(path: Path) -> Path:
    path = Path(path)
    _require_archive_suffix(path)
    try:
        size = path.stat().st_size
    except OSError as error:
        raise RigError(f"Cannot read rig archive {path}: {error}") from error
    if not path.is_file():
        raise RigError(f"Rig archive is not a regular file: {path}")
    if size > _MAX_ARCHIVE_FILE_BYTES:
        raise RigError(f"Rig archive exceeds {_MAX_ARCHIVE_FILE_BYTES} bytes: {path}")
    return path


def _require_archive_suffix(path: Path) -> None:
    if path.suffix.lower() != _ARCHIVE_SUFFIX:
        raise RigError(f"Rig archive must use the {_ARCHIVE_SUFFIX} suffix: {path}")


def _require_sha256(value: object, field: str) -> str:
    if not isinstance(value, str) or not _SHA256.fullmatch(value):
        raise RigError(f"{field} must be a lowercase SHA-256 digest")
    return value


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    try:
        with path.open("rb") as source:
            for chunk in iter(lambda: source.read(1 << 20), b""):
                digest.update(chunk)
    except OSError as error:
        raise RigError(f"Cannot hash rig archive {path}: {error}") from error
    return digest.hexdigest()
