"""reference: the identification agent — first of the two editorial passes.

A text-only agent reads the reconstructed manuscript and produces the
reference dossier: what the document is, and for each passage that tracks a
transmitted text, the controlling received wording with citation and
confidence. Emend consumes the dossier as its received tradition, so
open-ended discovery (including bounded web verification) happens here,
once, in an auditable artifact — never inside the emender.
"""

from __future__ import annotations

from palimpsest.factory import agent_cell
from palimpsest.factory.core.registry import register
from palimpsest.factory.core.station import Job, Station, StationResult
from palimpsest.factory.workspace.io import read_json
from palimpsest.factory.workspace.layout import doc_dir

TASK = (
    "Perform the task defined in AGENTS.md. Read evidence/manuscript.json, "
    "write out/reference.json exactly per the contract, then give the short "
    "final summary."
)


class Reference(Station):
    name = "reference"
    version = "reference/v1"
    grain = "manuscript"
    consumes = ("manuscript",)
    produces = "reference"
    uses_model = True

    def run(self, job: Job) -> StationResult:
        manuscript = read_json(job.path_of("manuscript"))
        sections = [
            {"heading": s["heading"], "original": s["original"]}
            for s in manuscript["sections"]
        ]
        workspace = agent_cell.stage_workspace(
            doc_dir(job.doc_id, job.library_root) / "runs" / "reference_agent",
            skill=job.config.prompt.text,
            evidence={"manuscript": {"sections": sections}},
            images=[],
        )
        run = agent_cell.run(
            workspace, TASK, model=job.config.model,
            timeout_s=int(job.config.options.get("timeout_s",
                                                 agent_cell.DEFAULT_TIMEOUT_S)),
        )
        artifact = agent_cell.read_artifact(workspace, "reference.json")
        return StationResult(
            payload={
                "doc_id": job.doc_id,
                "identification": artifact.get("identification", {}),
                "reference_points": artifact.get("reference_points", []),
                "editorial_notes": artifact.get("editorial_notes", []),
            },
            # the harness reports one blended figure; recorded as tokens_in
            tokens_in=run.tokens,
        )


register(Reference())
