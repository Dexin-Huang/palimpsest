"""Direct full-image transcription with one model call."""

from __future__ import annotations

from palimpsest.factory.core.registry import register
from palimpsest.factory.core.station import Job, Station, StationResult
from palimpsest.factory.gateway import GatewayError, ModelRequest, generate_json


_TRANSCRIPTION_SCHEMA = {
    "type": "object",
    "properties": {"transcription": {"type": "string"}},
    "required": ["transcription"],
    "additionalProperties": False,
}
_TRUNCATION_REASONS = ("MAX_TOKENS", "LENGTH", "INCOMPLETE")


class Transcribe(Station):
    """Send the raw archive image to one reader without preprocessing or tiling."""

    name = "transcribe"
    grain = "page"
    consumes = ("page_image",)
    produces = "page_transcription_draft"
    uses_model = True
    param_keys = frozenset(
        {
            "temperature",
            "max_output_tokens",
            "media_resolution",
            "thinking_level",
        }
    )
    production_dependencies = (
        "factory/config.py",
        "factory/gateway/__init__.py",
        "factory/gateway/client.py",
        "factory/gateway/omp.py",
        "factory/gateway/protocol.py",
    )

    def run(self, job: Job) -> StationResult:
        params = job.config.params
        response_value, response = generate_json(
            ModelRequest(
                model=job.config.model,
                prompt=job.config.prompt.text,
                images=(job.path_of("page_image"),),
                temperature=params.get("temperature", 0.1),
                max_output_tokens=params.get("max_output_tokens", 32768),
                media_resolution=params.get("media_resolution"),
                json_output=True,
                json_schema=_TRANSCRIPTION_SCHEMA,
                thinking_level=params.get("thinking_level"),
            )
        )
        if _is_truncated(response.finish_reason):
            raise GatewayError(
                "direct transcription was truncated",
                tokens_in=response.prompt_tokens,
                tokens_out=response.billable_output_tokens,
                cost_usd=response.cost_usd,
                finish_reason=response.finish_reason,
            )

        payload = {
            "doc_id": job.doc_id,
            "page_id": job.page_id,
            "page_seq": job.page.get("order", 0),
            "canvas_id": job.page.get("canvas_id", ""),
            "text": response_value["transcription"].strip(),
            "requested_model": job.config.model,
            "model": response.model,
            "finish_reason": response.finish_reason,
        }
        return StationResult(
            payload=payload,
            tokens_in=response.prompt_tokens,
            tokens_out=response.billable_output_tokens,
            cost_usd=response.cost_usd,
        )


def _is_truncated(finish_reason: str | None) -> bool:
    return bool(
        finish_reason
        and any(reason in finish_reason.upper() for reason in _TRUNCATION_REASONS)
    )


register(Transcribe())
