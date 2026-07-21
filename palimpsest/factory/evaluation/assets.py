"""Verified materialization of external evaluation assets."""

from __future__ import annotations

import hashlib
import os
import re
import time
from collections.abc import Iterable, Iterator, Mapping
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit
from uuid import uuid4

import requests

from palimpsest.factory.evaluation.suite import CaseAsset, EvaluationCase
from palimpsest.factory.intake import REQUEST_HEADERS

TIMEOUT_SECONDS = 60.0
_LOCK_WAIT_SECONDS = 2 * TIMEOUT_SECONDS
_CHUNK_SIZE = 1 << 16
_SHA256 = re.compile(r"[0-9a-f]{64}\Z")


class AssetFetchError(RuntimeError):
    """An evaluation asset could not be verified or materialized."""


@dataclass(frozen=True, slots=True)
class AssetFetchRecord:
    """Immutable result for one verified local or content-addressed asset."""

    sha256: str
    path: Path
    source: str | None
    status: Literal["fetched", "reused", "local"]


def _walk_assets(value: object) -> Iterator[CaseAsset]:
    if isinstance(value, CaseAsset):
        yield value
        return
    if isinstance(value, EvaluationCase):
        yield from _walk_assets(value.inputs)
        yield from _walk_assets(value.references)
        return
    if isinstance(value, Mapping):
        for child in value.values():
            yield from _walk_assets(child)
        return
    if isinstance(value, Iterable) and not isinstance(value, (str, bytes, bytearray)):
        for child in value:
            yield from _walk_assets(child)
        return
    raise AssetFetchError(
        f"Expected resolved evaluation cases or assets, got {type(value).__name__}"
    )


def _source_url(source: str) -> str:
    url = source[5:] if source.startswith("iiif:") else source
    parsed = urlsplit(url)
    if parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
        raise AssetFetchError("Asset source must be an http(s) or iiif:http(s) URL")
    return url


def _validate_asset(asset: CaseAsset) -> None:
    if not isinstance(asset.sha256, str) or not _SHA256.fullmatch(asset.sha256):
        raise AssetFetchError("Asset sha256 must be a lowercase SHA-256 digest")
    if (asset.path is None) == (asset.source is None):
        raise AssetFetchError("Asset must declare exactly one of path or source")
    if asset.path is not None and (not isinstance(asset.path, str) or not asset.path):
        raise AssetFetchError("Asset path must be a non-empty string")
    if asset.source is not None:
        if not isinstance(asset.source, str) or not asset.source:
            raise AssetFetchError("Asset source must be a non-empty string")
        _source_url(asset.source)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(_CHUNK_SIZE), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _verify_file(path: Path, expected: str, *, label: str) -> None:
    try:
        actual = _sha256_file(path)
    except OSError as error:
        raise AssetFetchError(f"Cannot verify {label}: {error}") from error
    if actual != expected:
        raise AssetFetchError(
            f"Hash mismatch for {label}: expected {expected}, got {actual}"
        )


def _local_asset_path(asset_root: Path, relative: str) -> Path:
    candidate = Path(relative)
    if (
        candidate.is_absolute()
        or "\\" in relative
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        raise AssetFetchError("Asset path must be a normalized relative path")
    root = asset_root.resolve()
    path = (root / candidate).resolve()
    if not path.is_relative_to(root):
        raise AssetFetchError("Asset path escapes the evaluation data root")
    return path


def _lock_file(handle: object, *, deadline: float) -> None:
    if os.name == "nt":
        import msvcrt

        while True:
            try:
                handle.seek(0)  # type: ignore[attr-defined]
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)  # type: ignore[attr-defined]
                return
            except OSError as error:
                if time.monotonic() >= deadline:
                    raise AssetFetchError(
                        "Timed out waiting for evaluation object lock"
                    ) from error
                time.sleep(0.05)
    else:
        import fcntl

        while True:
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)  # type: ignore[attr-defined]
                return
            except BlockingIOError as error:
                if time.monotonic() >= deadline:
                    raise AssetFetchError(
                        "Timed out waiting for evaluation object lock"
                    ) from error
                time.sleep(0.05)


def _unlock_file(handle: object) -> None:
    if os.name == "nt":
        import msvcrt

        handle.seek(0)  # type: ignore[attr-defined]
        msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)  # type: ignore[attr-defined]
    else:
        import fcntl

        fcntl.flock(handle.fileno(), fcntl.LOCK_UN)  # type: ignore[attr-defined]


@contextmanager
def _object_lock(object_root: Path, digest: str) -> Iterator[None]:
    lock_root = object_root / ".locks"
    lock_root.mkdir(parents=True, exist_ok=True)
    lock_path = lock_root / digest
    with lock_path.open("a+b") as handle:
        if handle.seek(0, os.SEEK_END) == 0:
            handle.write(b"\0")
            handle.flush()
            os.fsync(handle.fileno())
        _lock_file(handle, deadline=time.monotonic() + _LOCK_WAIT_SECONDS)
        try:
            yield
        finally:
            _unlock_file(handle)


def _fsync_directory(path: Path) -> None:
    if os.name == "nt":
        return
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _download_object(
    *,
    source: str,
    expected: str,
    destination: Path,
    session: requests.Session,
) -> None:
    url = _source_url(source)
    temporary = destination.parent / f".{expected}.{uuid4().hex}.tmp"
    try:
        with session.get(
            url,
            stream=True,
            timeout=TIMEOUT_SECONDS,
            headers=REQUEST_HEADERS,
        ) as response:
            response.raise_for_status()
            final_url = str(response.url)
            final = urlsplit(final_url)
            if final.scheme.lower() not in {"http", "https"} or not final.netloc:
                raise AssetFetchError(
                    f"Asset redirect resolved to unsupported URL: {final_url!r}"
                )
            digest = hashlib.sha256()
            with temporary.open("xb") as handle:
                for chunk in response.iter_content(chunk_size=_CHUNK_SIZE):
                    if chunk:
                        handle.write(chunk)
                        digest.update(chunk)
                handle.flush()
                os.fsync(handle.fileno())
        actual = digest.hexdigest()
        if actual != expected:
            raise AssetFetchError(
                f"Hash mismatch for {source!r}: expected {expected}, got {actual}"
            )
        if destination.exists():
            _verify_file(destination, expected, label=f"cached object {expected}")
            return
        os.replace(temporary, destination)
        _fsync_directory(destination.parent)
    except AssetFetchError:
        raise
    except (OSError, requests.RequestException) as error:
        raise AssetFetchError(f"Cannot fetch asset {source!r}: {error}") from error
    finally:
        try:
            temporary.unlink()
        except FileNotFoundError:
            pass
        except OSError as error:
            raise AssetFetchError(
                f"Cannot remove partial asset download {temporary}: {error}"
            ) from error


def _fetch_source(
    *,
    source: str,
    expected: str,
    object_root: Path,
    session: requests.Session,
) -> AssetFetchRecord:
    object_root.mkdir(parents=True, exist_ok=True)
    destination = object_root / expected
    with _object_lock(object_root, expected):
        if destination.exists():
            _verify_file(destination, expected, label=f"cached object {expected}")
            return AssetFetchRecord(expected, destination, source, "reused")
        _download_object(
            source=source,
            expected=expected,
            destination=destination,
            session=session,
        )
        return AssetFetchRecord(expected, destination, source, "fetched")


def fetch_assets(
    items: EvaluationCase | CaseAsset | Iterable[EvaluationCase | CaseAsset],
    *,
    object_root: str | Path,
    asset_root: str | Path,
    session: requests.Session | None = None,
) -> tuple[AssetFetchRecord, ...]:
    """Verify local assets and materialize declared source assets by digest.

    Declarations are fully validated before local files are read or network requests
    begin. Source assets sharing a digest are fetched once; declarations assigning
    different underlying URLs to one digest are rejected.
    """
    local_assets: dict[tuple[str, str], CaseAsset] = {}
    source_assets: dict[str, tuple[str, str]] = {}
    for asset in _walk_assets(items):
        _validate_asset(asset)
        if asset.path is not None:
            local_assets.setdefault((asset.sha256, asset.path), asset)
            continue
        assert asset.source is not None
        url = _source_url(asset.source)
        existing = source_assets.get(asset.sha256)
        if existing is not None and existing[0] != url:
            raise AssetFetchError(
                f"Conflicting source declarations for SHA-256 {asset.sha256}: "
                f"{existing[1]!r} and {asset.source!r}"
            )
        source_assets.setdefault(asset.sha256, (url, asset.source))

    resolved_asset_root = Path(asset_root)
    records: list[AssetFetchRecord] = []
    for (digest, relative), asset in local_assets.items():
        path = _local_asset_path(resolved_asset_root, relative)
        _verify_file(path, digest, label=f"local asset {relative!r}")
        records.append(AssetFetchRecord(digest, path, None, "local"))

    owned_session = session is None
    client = session if session is not None else requests.Session()
    try:
        for digest, (_, source) in source_assets.items():
            records.append(
                _fetch_source(
                    source=source,
                    expected=digest,
                    object_root=Path(object_root),
                    session=client,
                )
            )
    finally:
        if owned_session:
            client.close()
    return tuple(records)
