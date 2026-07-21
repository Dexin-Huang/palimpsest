"""Strict judge records kept separate from production station candidates."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from palimpsest.factory import prompt_store
from palimpsest.factory.evaluation.candidate import (
    RecordError,
    _load_yaml,
    _record_id,
    _reject_environment_values,
    _resolve_prompt,
    _schema_version,
    _strict_mapping,
    _string,
    content_fingerprint,
    default_model_identity,
    immutable_json,
)


def _resolve_registered(name: str, resolver: object, *, kind: str) -> object:
    if isinstance(resolver, Mapping):
        try:
            return resolver[name]
        except KeyError:
            raise RecordError(f"Unknown {kind}: {name!r}") from None
    lookup = resolver if callable(resolver) else getattr(resolver, "get", None)
    if not callable(lookup):
        raise TypeError(f"{kind} resolver must be a mapping, callable, or expose get()")
    try:
        value = lookup(name)
    except (KeyError, LookupError, ValueError) as error:
        raise RecordError(f"Unknown {kind}: {name!r}") from error
    if value is None:
        raise RecordError(f"Unknown {kind}: {name!r}")
    return value


@dataclass(frozen=True, slots=True)
class ResolvedJudge:
    schema_version: int
    id: str
    model: str
    model_identity: Literal["fixed", "moving"]
    prompt_name: str
    prompt_hash: str
    response_schema: str
    params: Mapping[str, object]
    fingerprint: str
    tracked: bool = True
    response_schema_definition: object = field(default=None, repr=False, compare=False)

    @property
    def can_auto_qualify(self) -> bool:
        return self.tracked and self.model_identity == "fixed"


def load_judge(
    path: str | Path,
    *,
    response_schema_resolver: object,
    prompt_resolver: Callable[[str], object] = prompt_store.load,
    model_identity_resolver: Callable[
        [str], Literal["fixed", "moving"]
    ] = default_model_identity,
    tracked: bool = True,
) -> ResolvedJudge:
    """Load a judge only after resolving its prompt and registered output schema."""
    record = _strict_mapping(
        _load_yaml(Path(path)),
        field="judge",
        required={
            "schema_version",
            "id",
            "model",
            "prompt",
            "response_schema",
            "params",
        },
    )
    _reject_environment_values(record)
    schema_version = _schema_version(record["schema_version"])
    judge_id = _record_id(record["id"], field="judge.id")
    model = _string(record["model"], field="judge.model")
    prompt_name = _record_id(record["prompt"], field="judge.prompt")
    prompt_name, prompt_hash = _resolve_prompt(prompt_name, prompt_resolver)
    response_schema = _record_id(
        record["response_schema"], field="judge.response_schema"
    )
    response_schema_definition = _resolve_registered(
        response_schema, response_schema_resolver, kind="response schema"
    )
    if not isinstance(record["params"], dict):
        raise RecordError("judge.params must be a mapping")
    params = immutable_json(record["params"], field="judge.params")
    assert isinstance(params, Mapping)
    model_identity = model_identity_resolver(model)
    if model_identity not in {"fixed", "moving"}:
        raise RecordError("model identity resolver must return 'fixed' or 'moving'")
    identity = {
        "schema_version": schema_version,
        "id": judge_id,
        "model": model,
        "model_identity": model_identity,
        "prompt": prompt_name,
        "prompt_hash": prompt_hash,
        "response_schema": response_schema,
        "params": params,
    }
    return ResolvedJudge(
        schema_version=schema_version,
        id=judge_id,
        model=model,
        model_identity=model_identity,
        prompt_name=prompt_name,
        prompt_hash=prompt_hash,
        response_schema=response_schema,
        params=params,
        fingerprint=content_fingerprint(identity),
        tracked=tracked,
        response_schema_definition=response_schema_definition,
    )
