"""Bench-only direct full-image reading socket.

One raw ``page_image``, one reader call: no preprocessing, no tiling, no
clean-image dependency, no adjudication. The production line reads through
the segmented ``read`` socket (page_image_clean + page_regions → one
page_transcription); this socket exists for the evaluation plane's
direct-transcription suites, which stage only a raw page image and want a
single unadjudicated reader pass.
"""

from __future__ import annotations

from palimpsest.factory.core.registry import register
from palimpsest.factory.core.station import Job, Station, StationResult
from palimpsest.factory.gateway import GatewayError, ModelRequest, generate_json
from palimpsest.factory.gateway.client import is_truncated
from palimpsest.factory.stations.read import READ_SCHEMA


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
                json_schema=READ_SCHEMA,
                thinking_level=params.get("thinking_level"),
            )
        )
        if is_truncated(response):
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


register(Transcribe())
