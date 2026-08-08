"""Cold, content-verified snapshots of authoritative local factory state."""

from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import zipfile
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Any, Iterable


class SnapshotError(RuntimeError):
    pass


_EXCLUDED_PREFIXES = ((".gateway-locks",),)
_EXCLUDED_SUFFIXES = ("-shm", "-wal", "-journal", ".lock")


def create_snapshot(
    library_root: Path,
    output: Path,
    *,
    database_paths: Iterable[Path],
) -> dict[str, Any]:
    """Write one atomic ZIP containing stable library state and online DB backups."""
    root = library_root.resolve()
    destination = output.resolve()
    if not root.is_dir():
        raise SnapshotError(f"Library root does not exist: {root}")
    if destination.exists():
        raise SnapshotError(f"Snapshot already exists: {destination}")
    if destination.is_relative_to(root):
        raise SnapshotError("Snapshot output must be outside the library root")
    databases = _database_relatives(root, database_paths)
    _refuse_running_operations(root, databases)

    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        dir=destination.parent, prefix=".palimpsest-snapshot-"
    ) as temporary_name:
        temporary_root = Path(temporary_name)
        backup_root = temporary_root / "databases"
        backup_root.mkdir()
        backups: dict[Path, Path] = {}
        for relative, source in databases.items():
            backup = backup_root / relative
            backup.parent.mkdir(parents=True, exist_ok=True)
            _backup_database(source, backup)
            backups[relative] = backup

        staged_archive = temporary_root / destination.name
        records: list[dict[str, Any]] = []
        with zipfile.ZipFile(
            staged_archive,
            "w",
            compression=zipfile.ZIP_STORED,
            allowZip64=True,
        ) as archive:
            for relative, source in _snapshot_files(root, backups):
                records.append(_write_member(archive, relative, source))
            manifest = {
                "schema_version": 1,
                "created_at": _utc_now(),
                "archive_kind": "palimpsest-library-snapshot",
                "database_paths": [path.as_posix() for path in sorted(backups)],
                "excluded": [
                    ".gateway-locks/",
                    "SQLite WAL/SHM/journal files",
                    "lock files",
                ],
                "files": records,
            }
            archive.writestr(
                "snapshot.json",
                json.dumps(manifest, ensure_ascii=False, sort_keys=True, indent=2)
                + "\n",
            )
        with staged_archive.open("rb+") as handle:
            os.fsync(handle.fileno())
        os.replace(staged_archive, destination)

    result = verify_snapshot(destination)
    result["archive"] = str(destination)
    result["archive_sha256"] = _file_sha256(destination)
    return result


def verify_snapshot(archive_path: Path) -> dict[str, Any]:
    """Verify the manifest, every payload digest, and each SQLite backup."""
    path = archive_path.resolve()
    if not path.is_file():
        raise SnapshotError(f"Snapshot does not exist: {path}")
    try:
        archive = zipfile.ZipFile(path)
    except zipfile.BadZipFile as error:
        raise SnapshotError(f"Invalid snapshot ZIP: {error}") from error
    with archive:
        names = archive.namelist()
        if len(names) != len(set(names)):
            raise SnapshotError("Snapshot contains duplicate member names")
        for name in names:
            _safe_member(name)
        if "snapshot.json" not in names:
            raise SnapshotError("Snapshot has no snapshot.json manifest")
        try:
            manifest = json.loads(archive.read("snapshot.json"))
        except (UnicodeError, json.JSONDecodeError) as error:
            raise SnapshotError(f"Invalid snapshot manifest: {error}") from error
        records = _validate_manifest(manifest)
        expected_names = {record["path"] for record in records} | {"snapshot.json"}
        if set(names) != expected_names:
            raise SnapshotError("Snapshot members do not exactly match its manifest")
        for record in records:
            digest = hashlib.sha256()
            size = 0
            with archive.open(record["path"]) as source:
                for chunk in iter(lambda: source.read(1024 * 1024), b""):
                    digest.update(chunk)
                    size += len(chunk)
            if size != record["bytes"] or digest.hexdigest() != record["sha256"]:
                raise SnapshotError(f"Snapshot payload mismatch: {record['path']}")
        _verify_archived_databases(archive, manifest["database_paths"])
    return {
        "files": len(records),
        "payload_bytes": sum(record["bytes"] for record in records),
        "databases": list(manifest["database_paths"]),
    }


def restore_snapshot(archive_path: Path, output: Path) -> dict[str, Any]:
    """Extract a verified snapshot into a new library root and verify it again."""
    result = verify_snapshot(archive_path)
    destination = output.resolve()
    if destination.exists():
        raise SnapshotError(f"Restore destination already exists: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        dir=destination.parent, prefix=".palimpsest-restore-"
    ) as temporary_name:
        staged = Path(temporary_name) / destination.name
        staged.mkdir()
        with zipfile.ZipFile(archive_path) as archive:
            manifest = json.loads(archive.read("snapshot.json"))
            for record in manifest["files"]:
                target = staged / Path(*PurePosixPath(record["path"]).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(record["path"]) as source, target.open("wb") as sink:
                    shutil.copyfileobj(source, sink, length=1024 * 1024)
                if (
                    target.stat().st_size != record["bytes"]
                    or _file_sha256(target) != record["sha256"]
                ):
                    raise SnapshotError(f"Restored payload mismatch: {record['path']}")
            for relative in manifest["database_paths"]:
                _check_database(staged / Path(*PurePosixPath(relative).parts))
        os.replace(staged, destination)
    return {**result, "restored_to": str(destination)}


def _database_relatives(root: Path, database_paths: Iterable[Path]) -> dict[Path, Path]:
    result: dict[Path, Path] = {}
    for raw_path in database_paths:
        source = raw_path.resolve()
        if not source.is_file():
            raise SnapshotError(f"Required database does not exist: {source}")
        try:
            relative = source.relative_to(root)
        except ValueError as error:
            raise SnapshotError(
                f"Database must be inside the library root: {source}"
            ) from error
        if relative in result:
            raise SnapshotError(f"Duplicate snapshot database: {source}")
        result[relative] = source
    return result


def _refuse_running_operations(root: Path, databases: dict[Path, Path]) -> None:
    queries = {
        Path("factory.db"): "SELECT COUNT(*) FROM work_runs WHERE status = 'running'",
        Path(
            "catalog.db"
        ): "SELECT COUNT(*) FROM catalog_sync_runs WHERE status = 'running'",
    }
    for relative, source in databases.items():
        query = queries.get(relative)
        if query is None:
            continue
        with closing(sqlite3.connect(source)) as connection:
            running = connection.execute(query).fetchone()[0]
        if running:
            raise SnapshotError(
                f"Cannot snapshot while {running} operation(s) are running in {relative}"
            )


def _backup_database(source: Path, destination: Path) -> None:
    with (
        closing(sqlite3.connect(source)) as live,
        closing(sqlite3.connect(destination)) as backup,
    ):
        live.backup(backup)
    _check_database(destination)


def _check_database(path: Path) -> None:
    with closing(
        sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    ) as connection:
        result = connection.execute("PRAGMA integrity_check").fetchone()[0]
    if result != "ok":
        raise SnapshotError(f"SQLite integrity check failed for {path}: {result}")


def _snapshot_files(root: Path, backups: dict[Path, Path]) -> list[tuple[Path, Path]]:
    files: dict[Path, Path] = {}
    database_paths = set(backups)
    for source in root.rglob("*"):
        if source.is_symlink():
            raise SnapshotError(f"Snapshot refuses symbolic links: {source}")
        if not source.is_file():
            continue
        relative = source.relative_to(root)
        if relative in database_paths or _excluded(relative):
            continue
        files[relative] = source
    files.update(backups)
    return sorted(files.items(), key=lambda item: item[0].as_posix())


def _excluded(relative: Path) -> bool:
    parts = relative.parts
    if any(parts[: len(prefix)] == prefix for prefix in _EXCLUDED_PREFIXES):
        return True
    return relative.name.endswith(_EXCLUDED_SUFFIXES)


def _write_member(
    archive: zipfile.ZipFile, relative: Path, source: Path
) -> dict[str, Any]:
    name = relative.as_posix()
    digest = hashlib.sha256()
    size = 0
    with (
        source.open("rb") as input_file,
        archive.open(name, "w", force_zip64=True) as output_file,
    ):
        for chunk in iter(lambda: input_file.read(1024 * 1024), b""):
            output_file.write(chunk)
            digest.update(chunk)
            size += len(chunk)
    return {"path": name, "bytes": size, "sha256": digest.hexdigest()}


def _validate_manifest(manifest: Any) -> list[dict[str, Any]]:
    if not isinstance(manifest, dict) or manifest.get("schema_version") != 1:
        raise SnapshotError("Unsupported snapshot manifest")
    if manifest.get("archive_kind") != "palimpsest-library-snapshot":
        raise SnapshotError("Snapshot manifest has the wrong archive kind")
    records = manifest.get("files")
    databases = manifest.get("database_paths")
    if not isinstance(records, list) or not isinstance(databases, list):
        raise SnapshotError("Snapshot manifest is missing files or databases")
    validated: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        if not isinstance(record, dict):
            raise SnapshotError("Snapshot file record must be an object")
        name = record.get("path")
        size = record.get("bytes")
        digest = record.get("sha256")
        if (
            not isinstance(name, str)
            or name in seen
            or not isinstance(size, int)
            or isinstance(size, bool)
            or size < 0
            or not isinstance(digest, str)
            or len(digest) != 64
        ):
            raise SnapshotError("Snapshot contains an invalid file record")
        _safe_member(name)
        seen.add(name)
        validated.append({"path": name, "bytes": size, "sha256": digest})
    for database in databases:
        if not isinstance(database, str) or database not in seen:
            raise SnapshotError("Snapshot names an invalid database path")
    return validated


def _verify_archived_databases(
    archive: zipfile.ZipFile, database_paths: list[str]
) -> None:
    with tempfile.TemporaryDirectory(prefix="palimpsest-snapshot-verify-") as name:
        root = Path(name)
        for relative in database_paths:
            target = root / Path(*PurePosixPath(relative).parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with archive.open(relative) as source, target.open("wb") as sink:
                shutil.copyfileobj(source, sink, length=1024 * 1024)
            _check_database(target)


def _safe_member(name: str) -> None:
    path = PurePosixPath(name)
    if path.is_absolute() or not path.parts or ".." in path.parts or "\\" in name:
        raise SnapshotError(f"Unsafe snapshot path: {name!r}")


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _utc_now() -> str:
    return (
        datetime.now(timezone.utc).isoformat(timespec="seconds").replace("+00:00", "Z")
    )
