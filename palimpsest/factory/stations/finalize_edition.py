"""finalize_edition: reconcile reader-facing prose with the edited text.

The diplomatic transcription and its uncertainty audit, reconstructed manuscript,
reference dossier, and emendation apparatus are already complete. A goal-directed
agent reviews the manuscript section by section and writes only the reader-facing
layer: the reader's note, headings, and translations. It cannot mutate any evidence
layer; ``publish`` combines its prose with those immutable inputs.
"""

from __future__ import annotations

from typing import Any

from palimpsest.factory import agent_cell
from palimpsest.factory.core.registry import register
from palimpsest.factory.core.station import Job, Station, StationResult
from palimpsest.factory.workspace.io import read_json
from palimpsest.factory.workspace.layout import doc_dir

TASK = (
    "Produce the final reader-facing edition defined in AGENTS.md. Work through "
    "every numbered section in order using evidence/manuscript.json, "
    "evidence/emendations.json, evidence/reference.json, and "
    "evidence/transcription_audits.json. Write out/edition.json exactly per the "
    "AGENTS.md output contract. Prefer node_repl filesystem APIs; if node_repl "
    "is unavailable, use another workspace-write mechanism rather than stopping. "
    "Then give the short final summary."
)


class FinalizeEdition(Station):
    name = "finalize_edition"

    grain = "manuscript"
    consumes = ("page_transcription", "manuscript", "reference", "emendations")
    produces = "edition"
    uses_model = True
    option_keys = frozenset({"timeout_s", "executor", "max_repairs"})
    production_dependencies = ("factory/agent_cell.py",)

    def run(self, job: Job) -> StationResult:
        manuscript = _without_provenance(read_json(job.path_of("manuscript")))
        reference = _without_provenance(read_json(job.path_of("reference")))
        emendations = _without_provenance(read_json(job.path_of("emendations")))
        transcription_audits = {
            "pages": [
                _without_provenance(
                    read_json(job.path_of("page_transcription", page["page_id"]))
                )
                for page in job.pages
            ]
        }

        workspace = agent_cell.stage_workspace(
            doc_dir(job.doc_id, job.library_root) / "runs" / "finalize_edition_agent",
            skill=job.config.prompt.text,
            evidence={
                "manuscript": manuscript,
                "reference": reference,
                "emendations": emendations,
                "transcription_audits": transcription_audits,
            },
            images=(),
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
        artifact = agent_cell.read_artifact(workspace, "edition.json")
        tokens = run.tokens

        failures = _edition_failures(manuscript, artifact)
        rounds = 0
        while failures and rounds < int(job.config.options.get("max_repairs", 1)):
            repair = agent_cell.resume(
                workspace,
                run.session_id,
                _repair_message(failures),
                timeout_s=timeout,
                executor=executor,
            )
            tokens += repair.tokens
            artifact = agent_cell.read_artifact(workspace, "edition.json")
            failures = _edition_failures(manuscript, artifact)
            rounds += 1
        if failures:
            raise ValueError(
                "final edition rejected after repair — "
                + "; ".join(failures[:5])
                + (f" (+{len(failures) - 5} more)" if len(failures) > 5 else "")
            )

        return StationResult(
            payload={
                "doc_id": job.doc_id,
                "readers_note": artifact["readers_note"].strip(),
                "sections": [
                    {
                        "section_index": section["section_index"],
                        "heading": section["heading"].strip(),
                        "translation": section["translation"].strip(),
                    }
                    for section in artifact["sections"]
                ],
            },
            tokens_in=tokens,
        )


def _without_provenance(value: dict[str, Any]) -> dict[str, Any]:
    value.pop("provenance", None)
    return value


def _edition_failures(manuscript: dict, edition: object) -> list[str]:
    if not isinstance(edition, dict):
        return ["artifact must be a JSON object"]

    failures: list[str] = []
    readers_note = edition.get("readers_note")
    if not isinstance(readers_note, str) or not readers_note.strip():
        failures.append("readers_note must be a non-empty string")

    sections = edition.get("sections")
    if not isinstance(sections, list):
        failures.append("sections must be an array")
        return failures

    expected_count = len(manuscript["sections"])
    if len(sections) != expected_count:
        failures.append(
            f"sections has {len(sections)} entries; expected {expected_count}"
        )

    for index, section in enumerate(sections):
        if not isinstance(section, dict):
            failures.append(f"sections[{index}] must be an object")
            continue
        if section.get("section_index") != index:
            failures.append(
                f"sections[{index}].section_index must be the integer {index}"
            )
        for field in ("heading", "translation"):
            value = section.get(field)
            if not isinstance(value, str) or not value.strip():
                failures.append(f"sections[{index}].{field} must be a non-empty string")
    return failures


def _repair_message(failures: list[str]) -> str:
    return (
        "The acceptance harness REJECTED out/edition.json. Fix every item below "
        "without changing evidence files or omitting any section:\n"
        + "\n".join(f"- {failure}" for failure in failures)
        + "\nRewrite out/edition.json; change nothing else."
    )


register(FinalizeEdition())
