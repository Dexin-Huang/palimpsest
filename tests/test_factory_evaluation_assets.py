from __future__ import annotations

import hashlib
import threading
import time
from collections import Counter
from concurrent.futures import ProcessPoolExecutor
from dataclasses import FrozenInstanceError
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import MappingProxyType
from typing import Iterator

import pytest
import requests

from palimpsest.factory.evaluation.assets import AssetFetchError, fetch_assets
from palimpsest.factory.evaluation.suite import CaseAsset, EvaluationCase
from palimpsest.factory.intake import REQUEST_HEADERS


class _FixtureServer(ThreadingHTTPServer):
    routes: dict[str, tuple[int, dict[str, str], bytes, float]]
    counts: Counter[str]
    user_agents: list[str | None]
    state_lock: threading.Lock


class _Handler(BaseHTTPRequestHandler):
    def do_GET(self) -> None:
        server = self.server
        assert isinstance(server, _FixtureServer)
        with server.state_lock:
            server.counts[self.path] += 1
            server.user_agents.append(self.headers.get("User-Agent"))
        status, headers, body, delay = server.routes[self.path]
        if delay:
            time.sleep(delay)
        self.send_response(status)
        for name, value in headers.items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        pass


@pytest.fixture
def http_server() -> Iterator[tuple[_FixtureServer, str]]:
    server = _FixtureServer(("127.0.0.1", 0), _Handler)
    server.routes = {}
    server.counts = Counter()
    server.user_agents = []
    server.state_lock = threading.Lock()
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    host, port = server.server_address
    try:
        yield server, f"http://{host}:{port}"
    finally:
        server.shutdown()
        thread.join()
        server.server_close()


def _digest(content: bytes) -> str:
    return hashlib.sha256(content).hexdigest()


def _fetch_in_process(
    source: str, digest: str, object_root: str, asset_root: str
) -> tuple[str, bytes]:
    record = fetch_assets(
        CaseAsset(sha256=digest, source=source),
        object_root=object_root,
        asset_root=asset_root,
    )[0]
    return record.status, record.path.read_bytes()


def _case(*assets: CaseAsset) -> EvaluationCase:
    return EvaluationCase(
        schema_version=1,
        case_id="case-1",
        doc_id="doc-1",
        page_id=None,
        pages=(),
        inputs=MappingProxyType(
            {
                "images": MappingProxyType(
                    {f"p{index}": asset for index, asset in enumerate(assets)}
                )
            }
        ),
        references=MappingProxyType({}),
        strata=(),
        license="test",
        adjudication=MappingProxyType({}),
        fingerprint="case-fingerprint",
    )


def _temporary_files(root: Path) -> list[Path]:
    return [
        path for path in root.rglob("*") if path.is_file() and path.suffix == ".tmp"
    ]


def test_fetches_iiif_source_once_by_digest_with_user_agent(
    tmp_path: Path, http_server: tuple[_FixtureServer, str]
) -> None:
    server, base_url = http_server
    content = b"verified image bytes"
    digest = _digest(content)
    server.routes["/image"] = (200, {}, content, 0.0)
    source = f"iiif:{base_url}/image"
    case = _case(
        CaseAsset(sha256=digest, source=source),
        CaseAsset(sha256=digest, source=source),
    )

    records = fetch_assets(
        case,
        object_root=tmp_path / "objects",
        asset_root=tmp_path,
    )

    assert len(records) == 1
    assert records[0].sha256 == digest
    assert records[0].path.read_bytes() == content
    assert records[0].source == source
    assert records[0].status == "fetched"
    assert server.counts == {"/image": 1}
    assert server.user_agents == [REQUEST_HEADERS["User-Agent"]]
    with pytest.raises(FrozenInstanceError):
        records[0].status = "reused"  # type: ignore[misc]


def test_reuses_correct_cached_object_without_request(tmp_path: Path) -> None:
    content = b"already cached"
    digest = _digest(content)
    object_root = tmp_path / "objects"
    object_root.mkdir()
    cached = object_root / digest
    cached.write_bytes(content)

    class NoRequestSession:
        def get(self, *args: object, **kwargs: object) -> object:
            raise AssertionError("cache reuse must not issue a request")

    records = fetch_assets(
        CaseAsset(sha256=digest, source="https://example.invalid/object"),
        object_root=object_root,
        asset_root=tmp_path,
        session=NoRequestSession(),  # type: ignore[arg-type]
    )

    assert records[0].status == "reused"
    assert records[0].path == cached
    assert cached.read_bytes() == content


def test_hash_mismatch_removes_download_without_publishing_object(
    tmp_path: Path, http_server: tuple[_FixtureServer, str]
) -> None:
    server, base_url = http_server
    server.routes["/wrong"] = (200, {}, b"wrong response", 0.0)
    expected = _digest(b"expected response")
    object_root = tmp_path / "objects"

    with pytest.raises(AssetFetchError, match="Hash mismatch"):
        fetch_assets(
            CaseAsset(sha256=expected, source=f"{base_url}/wrong"),
            object_root=object_root,
            asset_root=tmp_path,
        )

    assert not (object_root / expected).exists()
    assert _temporary_files(object_root) == []


def test_rejects_non_http_redirect_target_without_partial_file(tmp_path: Path) -> None:
    content = b"must not be written"
    digest = _digest(content)
    object_root = tmp_path / "objects"

    class RedirectResponse:
        url = "file:///private/redirected-object"

        def __enter__(self) -> RedirectResponse:
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def raise_for_status(self) -> None:
            pass

        def iter_content(self, *, chunk_size: int) -> Iterator[bytes]:
            yield content

    class RedirectSession:
        def get(self, *args: object, **kwargs: object) -> RedirectResponse:
            return RedirectResponse()

    with pytest.raises(AssetFetchError, match="unsupported URL"):
        fetch_assets(
            CaseAsset(sha256=digest, source="https://example.invalid/start"),
            object_root=object_root,
            asset_root=tmp_path,
            session=RedirectSession(),  # type: ignore[arg-type]
        )

    assert not (object_root / digest).exists()
    assert _temporary_files(object_root) == []


def test_conflicting_duplicate_sources_and_missing_hash_fail_before_request(
    tmp_path: Path,
) -> None:
    digest = "a" * 64

    class NoRequestSession:
        def get(self, *args: object, **kwargs: object) -> object:
            raise AssertionError("invalid declarations must fail before requests")

    session = NoRequestSession()
    with pytest.raises(AssetFetchError, match="Conflicting source declarations"):
        fetch_assets(
            (
                CaseAsset(sha256=digest, source="https://example.invalid/one"),
                CaseAsset(sha256=digest, source="https://example.invalid/two"),
            ),
            object_root=tmp_path / "objects",
            asset_root=tmp_path,
            session=session,  # type: ignore[arg-type]
        )
    with pytest.raises(AssetFetchError, match="sha256"):
        fetch_assets(
            CaseAsset(sha256="", source="https://example.invalid/one"),
            object_root=tmp_path / "objects",
            asset_root=tmp_path,
            session=session,  # type: ignore[arg-type]
        )
    assert not (tmp_path / "objects").exists()


def test_concurrent_fetches_converge_and_loser_verifies_winner(
    tmp_path: Path, http_server: tuple[_FixtureServer, str]
) -> None:
    server, base_url = http_server
    content = b"one immutable object"
    digest = _digest(content)
    server.routes["/slow"] = (200, {}, content, 0.5)
    source = f"{base_url}/slow"
    object_root = tmp_path / "objects"

    with ProcessPoolExecutor(max_workers=2) as executor:
        futures = (
            executor.submit(
                _fetch_in_process,
                source,
                digest,
                str(object_root),
                str(tmp_path),
            )
            for _ in range(2)
        )
        results = tuple(future.result(timeout=10) for future in futures)

    assert {status for status, _ in results} == {"fetched", "reused"}
    assert all(result == content for _, result in results)
    assert server.counts == {"/slow": 1}
    assert (object_root / digest).read_bytes() == content
    assert _temporary_files(object_root) == []


def test_local_assets_are_verified_in_place_and_never_copied(tmp_path: Path) -> None:
    asset_root = tmp_path / "data"
    asset_root.mkdir()
    local = asset_root / "gold.txt"
    local.write_bytes(b"gold content")
    digest = _digest(b"gold content")
    object_root = tmp_path / "objects"

    records = fetch_assets(
        CaseAsset(sha256=digest, path="gold.txt"),
        object_root=object_root,
        asset_root=asset_root,
    )

    assert records[0].status == "local"
    assert records[0].path == local.resolve()
    assert not object_root.exists()

    local.write_bytes(b"drifted content")
    with pytest.raises(AssetFetchError, match="Hash mismatch"):
        fetch_assets(
            CaseAsset(sha256=digest, path="gold.txt"),
            object_root=object_root,
            asset_root=asset_root,
        )
    assert not object_root.exists()


def test_stream_failure_cleans_partial_download(tmp_path: Path) -> None:
    expected = _digest(b"complete response")
    object_root = tmp_path / "objects"

    class PartialResponse:
        url = "https://example.invalid/object"

        def __enter__(self) -> PartialResponse:
            return self

        def __exit__(self, *args: object) -> None:
            pass

        def raise_for_status(self) -> None:
            pass

        def iter_content(self, *, chunk_size: int) -> Iterator[bytes]:
            yield b"partial"
            raise requests.ConnectionError("connection closed")

    class PartialSession:
        def get(self, *args: object, **kwargs: object) -> PartialResponse:
            return PartialResponse()

    with pytest.raises(AssetFetchError, match="Cannot fetch asset"):
        fetch_assets(
            CaseAsset(sha256=expected, source="https://example.invalid/object"),
            object_root=object_root,
            asset_root=tmp_path,
            session=PartialSession(),  # type: ignore[arg-type]
        )

    assert not (object_root / expected).exists()
    assert _temporary_files(object_root) == []
