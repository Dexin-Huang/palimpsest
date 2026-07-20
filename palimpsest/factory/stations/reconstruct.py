"""reconstruct: plan the manuscript's structure with one model call, then
assemble continuous texts deterministically.

The model decides sections, page joins, and a reader's note — it never
rewrites the text. Assembly applies its join decisions to the page texts in
code, so the reconstructed manuscript is verbatim what the line produced,
with every cross-page decision auditable in ``joins``.
"""

from __future__ import annotations

import json

from palimpsest.factory.core.registry import register
from palimpsest.factory.core.station import Job, Station, StationResult
from palimpsest.factory.gateway import ModelRequest, generate_json
from palimpsest.factory.workspace.io import read_json

_FLOW_JOINS = {"hyphenation_repair", "sentence_continuation"}

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "sections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "heading": {"type": "string"},
                    "from_page": {"type": "string"},
                    "to_page": {"type": "string"},
                },
                "required": ["heading", "from_page", "to_page"],
            },
        },
        "joins": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "from_page": {"type": "string"},
                    "to_page": {"type": "string"},
                    "kind": {
                        "type": "string",
                        "enum": [
                            "hyphenation_repair",
                            "sentence_continuation",
                            "paragraph_break",
                            "section_break",
                        ],
                    },
                    "rationale": {"type": "string"},
                },
                "required": ["from_page", "to_page", "kind"],
            },
        },
        "readers_note": {"type": "string"},
    },
    "required": ["sections", "joins", "readers_note"],
}


class Reconstruct(Station):
    name = "reconstruct"

    grain = "manuscript"
    consumes = ("page_assembled",)
    produces = "manuscript"
    uses_model = True
    param_keys = frozenset({"temperature", "max_output_tokens"})

    def run(self, job: Job) -> StationResult:
        pages = [
            read_json(job.path_of("page_assembled", page["page_id"]))
            for page in job.pages
        ]
        plan_text = "\n\n".join(_page_block(page) for page in pages)
        plan, response = generate_json(
            ModelRequest(
                model=job.config.model,
                prompt=job.config.prompt.text + "\n\n" + plan_text,
                temperature=job.config.params.get("temperature", 0.1),
                max_output_tokens=job.config.params.get("max_output_tokens", 32768),
                json_output=True,
                json_schema=PLAN_SCHEMA,
            )
        )

        by_id = {page["page_id"]: page for page in pages}
        order = [page["page_id"] for page in pages]
        joins = {(j["from_page"], j["to_page"]): j for j in plan.get("joins", [])}

        sections = []
        for section in plan.get("sections", []) or [
            {"heading": "Text", "from_page": order[0], "to_page": order[-1]}
        ]:
            span = order[
                order.index(section["from_page"]) : order.index(section["to_page"]) + 1
            ]
            sections.append(
                {
                    "heading": section["heading"],
                    "pages": {"from": section["from_page"], "to": section["to_page"]},
                    "original": _assemble(span, by_id, joins, "original"),
                    "translation": _assemble(span, by_id, joins, "translation"),
                }
            )

        return StationResult(
            payload={
                "doc_id": job.doc_id,
                "readers_note": plan.get("readers_note", ""),
                "sections": sections,
                "joins": plan.get("joins", []),
            },
            tokens_in=response.prompt_tokens,
            tokens_out=response.output_tokens,
            cost_usd=response.cost_usd,
        )


def _page_block(page: dict) -> str:
    flags = page["translation"].get("flags", {})
    return (
        f"[{page['page_id']}] flags={json.dumps(flags)}\n"
        f"ORIGINAL:\n{page['original']['text']}\n"
        f"TRANSLATION:\n{page['translation']['text']}"
    )


def _assemble(span: list[str], by_id: dict, joins: dict, side: str) -> str:
    parts: list[str] = []
    for index, page_id in enumerate(span):
        text = by_id[page_id][side]["text"].strip()
        if index == 0:
            parts.append(text)
            continue
        join = joins.get((span[index - 1], page_id), {})
        if join.get("kind") == "hyphenation_repair":
            parts[-1] = parts[-1].rstrip("-¬")
            parts.append(text)
            glue = ""
        elif join.get("kind") in _FLOW_JOINS:
            parts.append(text)
            glue = " "
        else:
            parts.append(text)
            glue = "\n\n"
        parts[-2:] = [parts[-2] + glue + parts[-1]]
    return parts[0] if parts else ""


register(Reconstruct())
