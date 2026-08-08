"""Publish immutable publication bundles through an S3-compatible object store."""

from __future__ import annotations

import json
import mimetypes
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


class PublicationStoreError(RuntimeError):
    """The publication store could not accept or verify a release."""


@dataclass(frozen=True)
class PublishedRelease:
    bundle_id: str
    object_uri: str
    public_url: str | None


def publish_bundle(
    bundle_root: Path,
    *,
    bundle_id: str,
    bucket: str,
    profile: str,
    endpoint_url: str,
    public_base_url: str | None = None,
) -> PublishedRelease:
    """Upload one bundle to its content-addressed prefix and verify its inventory."""

    aws = shutil.which("aws")
    if aws is None:
        raise PublicationStoreError(
            "AWS CLI not found on PATH; install it before publishing"
        )
    if not bundle_root.is_dir():
        raise PublicationStoreError(f"Publication bundle not found: {bundle_root}")

    prefix = f"releases/{bundle_id}/"
    object_uri = f"s3://{bucket}/{prefix}"
    files = sorted(path for path in bundle_root.rglob("*") if path.is_file())
    expected = {
        prefix + path.relative_to(bundle_root).as_posix(): path.stat().st_size
        for path in files
    }
    for path in files:
        key = prefix + path.relative_to(bundle_root).as_posix()
        content_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        _run_aws(
            aws,
            [
                "s3api",
                "put-object",
                "--bucket",
                bucket,
                "--key",
                key,
                "--body",
                str(path.resolve()),
                "--content-type",
                content_type,
                "--profile",
                profile,
                "--endpoint-url",
                endpoint_url,
            ],
            label=f"upload {key}",
        )
    listing = _run_aws(
        aws,
        [
            "s3api",
            "list-objects-v2",
            "--bucket",
            bucket,
            "--prefix",
            prefix,
            "--profile",
            profile,
            "--endpoint-url",
            endpoint_url,
            "--output",
            "json",
        ],
        label="verification",
    )
    try:
        payload = json.loads(listing)
        actual = {item["Key"]: item["Size"] for item in payload.get("Contents", [])}
    except (json.JSONDecodeError, KeyError, TypeError) as error:
        raise PublicationStoreError(
            "R2 verification returned an invalid object listing"
        ) from error
    if actual != expected:
        missing = sorted(expected.keys() - actual.keys())
        extra = sorted(actual.keys() - expected.keys())
        changed = sorted(
            key
            for key in expected.keys() & actual.keys()
            if expected[key] != actual[key]
        )
        raise PublicationStoreError(
            "R2 release inventory mismatch: "
            f"missing={missing[:5]} extra={extra[:5]} size_changed={changed[:5]}"
        )

    public_url = None
    if public_base_url is not None:
        public_url = f"{public_base_url.rstrip('/')}/{prefix}"
    return PublishedRelease(
        bundle_id=bundle_id,
        object_uri=object_uri,
        public_url=public_url,
    )


def _run_aws(aws: str, arguments: list[str], *, label: str) -> str:
    try:
        completed = subprocess.run(
            [aws, *arguments],
            check=False,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
        )
    except OSError as error:
        raise PublicationStoreError(f"R2 {label} could not start: {error}") from error
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout).strip()[-2000:]
        raise PublicationStoreError(f"R2 {label} failed: {detail}")
    return completed.stdout
