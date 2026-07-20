"""emend: the emendation agent — second of the two editorial passes.

An agent works inside the cell's staged workspace: the manuscript sections,
the seam variant pairs (same ink transcribed twice at overlapping
captures), the reference dossier, and the page photographs it can zoom into
by cropping. It produces an emended reading per section with a full
apparatus. The diplomatic transcription and the manuscript's verbatim
sections are never edited — this layer sits beside them, exactly like a
critical edition's text sits above its apparatus.

Acceptance is machine-checked (apparatus.py): every departure of the
reading from the original must be covered by an anchored apparatus entry,
and systematic substitutions must be swept across the whole text. One
rejection report goes back into the agent's own session for repair; a
second coverage failure fails the cell.
"""

from __future__ import annotations

from palimpsest.factory import agent_cell
from palimpsest.factory.apparatus import coverage_failures, systematic_sweeps
from palimpsest.factory.core.registry import register
from palimpsest.factory.core.station import Job, Station, StationResult
from palimpsest.factory.workspace.io import read_json
from palimpsest.factory.workspace.layout import doc_dir

TASK = (
    "Perform the emendation task defined in AGENTS.md, following its passes "
    "exactly. Evidence: evidence/manuscript.json, evidence/variants.json, "
    "and the reference dossier evidence/reference.json. Photographs: "
    "images/ (attached; to zoom a disputed span, crop with python and view "
    "the crop, writing crops to out/). Write the finished artifact to "
    "out/emendations.json exactly per the AGENTS.md output contract, then "
    "give the short final summary."
)


class Emend(Station):
    name = "emend"

    grain = "manuscript"
    consumes = ("manuscript", "reference", "page_assembled", "page_image_clean")
    produces = "emendations"
    uses_model = True
    option_keys = frozenset({"timeout_s", "executor", "max_repairs"})

    def run(self, job: Job) -> StationResult:
        manuscript = read_json(job.path_of("manuscript"))
        reference = read_json(job.path_of("reference"))
        reference.pop("provenance", None)  # testimony for the agent, not bookkeeping
        sections = [
            {"heading": s["heading"], "original": s["original"]}
            for s in manuscript["sections"]
        ]

        workspace = agent_cell.stage_workspace(
            doc_dir(job.doc_id, job.library_root) / "runs" / "emend_agent",
            skill=job.config.prompt.text,
            evidence={
                "manuscript": {"sections": sections},
                "variants": {"variants": self._seam_variants(job)},
                "reference": reference,
            },
            images=[
                job.path_of("page_image_clean", page["page_id"]) for page in job.pages
            ],
        )
        timeout = int(job.config.options.get("timeout_s", agent_cell.DEFAULT_TIMEOUT_S))
        executor = str(job.config.options.get("executor", "codex"))
        run = agent_cell.run(
            workspace,
            TASK,
            model=job.config.model,
            timeout_s=timeout,
            executor=executor,
        )
        artifact = agent_cell.read_artifact(workspace, "emendations.json")
        tokens = run.tokens

        failures = coverage_failures(sections, artifact)
        sweeps = systematic_sweeps(sections, artifact)  # advisory, first round only
        rounds = 0
        while (failures or sweeps) and rounds < int(
            job.config.options.get("max_repairs", 2)
        ):
            repair = agent_cell.resume(
                workspace,
                run.session_id,
                _repair_message(failures, sweeps),
                timeout_s=timeout,
                executor=executor,
            )
            tokens += repair.tokens
            artifact = agent_cell.read_artifact(workspace, "emendations.json")
            failures = coverage_failures(sections, artifact)
            sweeps = []
            rounds += 1
        if failures:
            raise ValueError(
                "emendation rejected after repair — "
                + "; ".join(failures[:5])
                + (f" (+{len(failures) - 5} more)" if len(failures) > 5 else "")
            )

        return StationResult(
            payload={
                "doc_id": job.doc_id,
                "sections": [
                    {"heading": ours["heading"], "reading": theirs["reading"]}
                    for ours, theirs in zip(
                        manuscript["sections"], artifact["sections"]
                    )
                ],
                "apparatus": artifact["apparatus"],
            },
            # the harness reports one blended figure; recorded as tokens_in
            tokens_in=tokens,
        )

    def _seam_variants(self, job: Job) -> list[dict]:
        """The two-vote pairs: at each trimmed seam, the kept tail of the
        previous capture and the dropped duplicate of this capture."""
        variants = []
        pages = sorted(job.pages, key=lambda p: p.get("order", 0))
        for index, page in enumerate(pages):
            assembled = read_json(job.path_of("page_assembled", page["page_id"]))
            seam = (assembled.get("original") or {}).get("seam")
            if not seam or not seam.get("dropped_text") or index == 0:
                continue
            previous = read_json(
                job.path_of("page_assembled", pages[index - 1]["page_id"])
            )
            kept = [
                line
                for line in previous["original"]["text"].splitlines()
                if line.strip()
            ][-seam["lines"] :]
            variants.append(
                {
                    "at_seam_between": [pages[index - 1]["page_id"], page["page_id"]],
                    "kept_reading": "\n".join(kept),
                    "duplicate_reading": seam["dropped_text"],
                    "note": "same physical columns, two independent transcriptions",
                }
            )
        return variants


def _repair_message(failures: list[str], sweeps: list[str]) -> str:
    parts = ["The acceptance harness reviewed out/emendations.json."]
    if failures:
        parts.append(
            "REJECTED — fix each item: for every UNCOVERED change, either "
            "revert the reading to the original text (mandatory for "
            "orthographic normalizations) or add the missing apparatus entry "
            "if the change is a genuine, evidenced emendation. Fix unanchored "
            "entries so their snippets match the text exactly.\n"
            + "\n".join(f"- {f}" for f in failures)
        )
    if sweeps:
        parts.append(
            "CONSISTENCY SWEEP — for each item, treat the surviving "
            "instances or record in the apparatus why they stand:\n"
            + "\n".join(f"- {s}" for s in sweeps)
        )
    parts.append("Rewrite out/emendations.json; change nothing else.")
    return "\n\n".join(parts)


register(Emend())
