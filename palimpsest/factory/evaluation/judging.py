"""Blind structured execution for versioned evaluation judges."""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from typing import Protocol

from palimpsest.factory import prompt_store
from palimpsest.factory.evaluation.judge import ResolvedJudge
from palimpsest.factory.evaluation.response_schemas import (
    PairwisePreference,
    ResponseSchema,
)
from palimpsest.factory.gateway.client import generate_json
from palimpsest.factory.gateway.protocol import (
    ImageContent,
    ModelRequest,
    ModelResponse,
)


@dataclass(frozen=True, slots=True)
class JudgeExecutionResult:
    response: PairwisePreference
    model: str
    prompt_tokens: int | None
    output_tokens: int | None
    thought_tokens: int | None
    total_tokens: int | None
    cost_usd: float | None


class JudgeExecutionError(ValueError):
    """A paid judge response failed local validation without losing its usage."""

    def __init__(self, message: str, response: ModelResponse) -> None:
        super().__init__(message)
        self.prompt_tokens = response.prompt_tokens
        self.output_tokens = response.output_tokens
        self.thought_tokens = response.thought_tokens
        self.total_tokens = response.total_tokens
        self.cost_usd = response.cost_usd


class JudgeExecutor(Protocol):
    """The only paid seam used by the evaluation runner for model judging."""

    def execute(
        self,
        *,
        judge: ResolvedJudge,
        source_image: bytes,
        source_mime: str,
        text_a: str,
        text_b: str,
    ) -> JudgeExecutionResult: ...


class GatewayJudgeExecutor:
    """Issue one blinded structured request through the factory gateway."""

    def __init__(
        self,
        generate: Callable[..., tuple[object, ModelResponse]] = generate_json,
    ) -> None:
        self._generate = generate

    def execute(
        self,
        *,
        judge: ResolvedJudge,
        source_image: bytes,
        source_mime: str,
        text_a: str,
        text_b: str,
    ) -> JudgeExecutionResult:
        prompt = prompt_store.load(judge.prompt_name)
        if prompt.sha256 != judge.prompt_hash:
            raise ValueError(
                f"Judge prompt hash mismatch for {judge.id!r}: "
                f"expected {judge.prompt_hash}, got {prompt.sha256}"
            )
        schema = judge.response_schema_definition
        if (
            not isinstance(schema, ResponseSchema)
            or schema.name != judge.response_schema
        ):
            raise TypeError(f"Judge {judge.id!r} has no executable response schema")
        rendered = _render_blind_prompt(prompt.text, text_a=text_a, text_b=text_b)
        request = _model_request(
            judge,
            prompt=rendered,
            image=ImageContent(source_image, source_mime),
            schema=schema.json_schema,
        )
        raw, provider = self._generate(request, attempts=1)
        if provider.model != judge.model:
            raise JudgeExecutionError(
                f"Judge provider returned model {provider.model!r}, expected {judge.model!r}",
                provider,
            )
        try:
            response = schema.validate(raw)
        except (TypeError, ValueError) as error:
            raise JudgeExecutionError(str(error), provider) from error
        return JudgeExecutionResult(
            response=response,
            model=provider.model,
            prompt_tokens=provider.prompt_tokens,
            output_tokens=provider.output_tokens,
            thought_tokens=provider.thought_tokens,
            total_tokens=provider.total_tokens,
            cost_usd=provider.cost_usd,
        )


def _render_blind_prompt(template: str, *, text_a: str, text_b: str) -> str:
    markers = ("{{TRANSCRIPTION_A}}", "{{TRANSCRIPTION_B}}")
    if any(template.count(marker) != 1 for marker in markers):
        raise ValueError(
            "Judge prompt must contain each transcription marker exactly once"
        )
    return template.replace(markers[0], text_a or "(empty)").replace(
        markers[1], text_b or "(empty)"
    )


def _model_request(
    judge: ResolvedJudge,
    *,
    prompt: str,
    image: ImageContent,
    schema: Mapping[str, object],
) -> ModelRequest:
    allowed = {
        "max_output_tokens",
        "media_resolution",
        "thinking_level",
        "allow_empty",
    }
    unknown = set(judge.params) - allowed
    if unknown:
        raise ValueError(f"Unsupported judge parameters: {sorted(unknown)}")
    params = dict(judge.params)
    return ModelRequest(
        model=judge.model,
        prompt=prompt,
        images=(image,),
        json_output=True,
        json_schema=schema,
        **params,
    )
