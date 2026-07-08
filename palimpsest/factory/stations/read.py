"""read: VLM transcription of one page image (lifted from transcribe.py)."""

from __future__ import annotations

from palimpsest.factory.core.registry import register
from palimpsest.factory.core.station import Job, Station, StationResult
from palimpsest.factory.gateway.client import ModelRequest, generate

DEFAULT_SYSTEM_PROMPT = (
    "You are an expert paleographer transcribing digitized manuscript pages."
)


class Read(Station):
    name = "read"
    version = "read/v1"
    grain = "page"
    consumes = ("page_image_clean",)
    produces = "page_transcription"
    uses_model = True

    def run(self, job: Job) -> StationResult:
        params = job.config.params
        response = generate(ModelRequest(
            model=job.config.model,
            prompt=job.config.prompt.text,
            system=params.get("system", DEFAULT_SYSTEM_PROMPT),
            images=(job.path_of("page_image_clean"),),
            temperature=params.get("temperature", 0.1),
            max_output_tokens=params.get("max_output_tokens", 32768),
            media_resolution=params.get("media_resolution"),
        ))
        return StationResult(
            payload={
                "doc_id": job.doc_id,
                "page_id": job.page_id,
                "page_seq": job.page.get("order", 0),
                "canvas_id": job.page.get("canvas_id", ""),
                "text": response.text,
                "regions": [],
            },
            tokens_in=response.prompt_tokens,
            tokens_out=response.output_tokens,
            cost_usd=response.cost_usd,
        )


register(Read())
