"""Deterministic read candidates carrying exact inline OMP extension bytes.

The exodia harness renders ``read`` ``omp_extension`` candidates into
OMP agent extensions (the instrumented rig is driven by tracked candidate
YAMLs through ``bench run``); the production rig uses its recipe-bound
extension.
"""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from palimpsest.factory.evaluation.candidate import load_candidate
from palimpsest.factory.stations.read_omp import MAX_EXTENSION_BYTES

OMP_EXTENSION_MEDIA_TYPE = "application/vnd.exodia.omp-extension+typescript"
DEFAULT_PROMPT = "read/zh/full_image"
_ROLES = frozenset({"baseline", "challenger"})


@dataclass(frozen=True, slots=True)
class RenderedCandidate:
    candidate_path: Path
    metadata_path: Path
    extension_path: Path
    source_sha256: str
    fingerprint: str


def render_candidate(
    source_bytes: bytes,
    *,
    role: Literal["baseline", "challenger"],
    model: str,
    output_dir: Path,
    prompt: str = DEFAULT_PROMPT,
    variant: str = "omp_extension",
    tool_bindings: tuple[Mapping[str, str], ...] | None = None,
) -> RenderedCandidate:
    """Render one immutable Candidate and exact content-addressed source copy."""

    if not isinstance(source_bytes, bytes):
        raise TypeError("source_bytes must be bytes")
    if len(source_bytes) > MAX_EXTENSION_BYTES:
        raise ValueError(f"extension source exceeds {MAX_EXTENSION_BYTES} bytes")
    try:
        source = source_bytes.decode("utf-8")
    except UnicodeDecodeError as error:
        raise ValueError("extension source must be strict UTF-8") from error
    if not source.strip():
        raise ValueError("extension source must not be empty")
    if role not in _ROLES:
        raise ValueError("role must be baseline or challenger")
    if not isinstance(model, str) or not model.strip():
        raise ValueError("model must be a non-empty string")
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be a non-empty string")
    if not isinstance(variant, str) or not variant.strip():
        raise ValueError("variant must be a non-empty string")
    validated_tool_bindings: tuple[dict[str, str], ...] | None = None
    if tool_bindings is not None:
        if not isinstance(tool_bindings, tuple) or not tool_bindings:
            raise ValueError("tool_bindings must be a non-empty tuple when present")
        previous: str | None = None
        collected: list[dict[str, str]] = []
        for index, raw_binding in enumerate(tool_bindings):
            field = f"tool_bindings[{index}]"
            if not isinstance(raw_binding, Mapping) or set(raw_binding) != {
                "id",
                "kind",
                "model",
            }:
                raise ValueError(f"{field} must contain only id, kind, and model")
            binding: dict[str, str] = {}
            for key in ("id", "kind", "model"):
                value = raw_binding[key]
                if not isinstance(value, str) or not value.strip():
                    raise ValueError(f"{field}.{key} must be a non-empty string")
                binding[key] = value
            if previous is not None and previous >= binding["id"]:
                raise ValueError("tool_bindings must be sorted and unique by id")
            if binding["kind"] != "draft_model":
                raise ValueError(f"{field}.kind is not supported")
            previous = binding["id"]
            collected.append(binding)
        validated_tool_bindings = tuple(collected)

    source_sha256 = hashlib.sha256(source_bytes).hexdigest()
    candidate_id = f"read/harness-autoresearch/{role}-{source_sha256}"
    candidate_bytes = _candidate_yaml(
        candidate_id=candidate_id,
        model=model,
        prompt=prompt,
        role=role,
        source=source,
        source_sha256=source_sha256,
        variant=variant,
        tool_bindings=validated_tool_bindings,
    )
    candidate_sha256 = hashlib.sha256(candidate_bytes).hexdigest()

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    extension_path = output_dir / f"{source_sha256}.transcription.ts"
    candidate_path = output_dir / f"{candidate_sha256}.candidate.yaml"
    _write_immutable(extension_path, source_bytes)
    _write_immutable(candidate_path, candidate_bytes)

    candidate = load_candidate(candidate_path)
    rendered_source = candidate.options.get("extension_source")
    if (
        not isinstance(rendered_source, str)
        or rendered_source.encode("utf-8") != source_bytes
    ):
        raise RuntimeError("rendered Candidate did not preserve exact extension bytes")

    metadata = {
        "schema_version": 1,
        "role": role,
        "source": {
            "file": extension_path.name,
            "media_type": OMP_EXTENSION_MEDIA_TYPE,
            "sha256": source_sha256,
            "size_bytes": len(source_bytes),
        },
        "candidate": {
            "file": candidate_path.name,
            "fingerprint": candidate.fingerprint,
            "id": candidate.id,
            "sha256": candidate_sha256,
        },
        "model": model,
        "prompt": prompt,
        "variant": variant,
        **(
            {}
            if validated_tool_bindings is None
            else {"tool_bindings": list(validated_tool_bindings)}
        ),
    }
    metadata_bytes = (
        json.dumps(metadata, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
        + "\n"
    ).encode("utf-8")
    metadata_sha256 = hashlib.sha256(metadata_bytes).hexdigest()
    metadata_path = output_dir / f"{metadata_sha256}.metadata.json"
    _write_immutable(metadata_path, metadata_bytes)

    return RenderedCandidate(
        candidate_path=candidate_path,
        metadata_path=metadata_path,
        extension_path=extension_path,
        source_sha256=source_sha256,
        fingerprint=candidate.fingerprint,
    )


def _candidate_yaml(
    *,
    candidate_id: str,
    model: str,
    prompt: str,
    role: str,
    source: str,
    source_sha256: str,
    variant: str,
    tool_bindings: tuple[Mapping[str, str], ...] | None,
) -> bytes:
    def scalar(value: str) -> str:
        return json.dumps(value, ensure_ascii=False)

    notes = f"harness autoresearch {role}; extension sha256 {source_sha256}"
    lines = [
        "schema_version: 1",
        f"id: {scalar(candidate_id)}",
        'station: "read"',
        f"variant: {scalar(variant)}",
        f"model: {scalar(model)}",
        f"prompt: {scalar(prompt)}",
        "params: {}",
        "options:",
        f"  extension_source: {scalar(source)}",
    ]
    if tool_bindings is not None:
        lines.append(
            "  tool_bindings: "
            + json.dumps(
                tool_bindings,
                ensure_ascii=False,
                sort_keys=True,
                separators=(",", ":"),
            )
        )
    lines.extend((f"notes: {scalar(notes)}", ""))
    return "\n".join(lines).encode("utf-8")


def _write_immutable(path: Path, content: bytes) -> None:
    try:
        with path.open("xb") as stream:
            stream.write(content)
    except FileExistsError:
        if path.read_bytes() != content:
            raise FileExistsError(
                f"content-addressed output already exists with different bytes: {path}"
            ) from None
