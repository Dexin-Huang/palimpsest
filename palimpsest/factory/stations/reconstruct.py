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
    production_dependencies = (
        "factory/gateway/__init__.py",
        "factory/gateway/client.py",
        "factory/gateway/omp.py",
        "factory/gateway/protocol.py",
        "factory/usage.py",
    )

    def run(self, job: Job) -> StationResult:
        pages = [
            read_json(job.path_of("page_assembled", page["page_id"]))
            for page in job.pages
        ]
        if not pages:
            raise ValueError("Reconstruction requires at least one assembled page")
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
        joins = _index_joins(order, plan.get("joins", []))

        sections = []
        for section in plan.get("sections", []) or [
            {"heading": "Text", "from_page": order[0], "to_page": order[-1]}
        ]:
            span = _section_span(order, section)
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
            tokens_out=response.billable_output_tokens,
            cost_usd=response.cost_usd,
        )


def _section_span(order: list[str], section: dict) -> list[str]:
    start_page = section["from_page"]
    end_page = section["to_page"]
    try:
        start = order.index(start_page)
        end = order.index(end_page)
    except ValueError as error:
        raise ValueError(
            f"Reconstruction section {section['heading']!r} references "
            f"an unknown page: {error.args[0]!r}"
        ) from error
    if start > end:
        raise ValueError(
            f"Reconstruction section {section['heading']!r} runs backward "
            f"from {start_page!r} to {end_page!r}"
        )
    return order[start : end + 1]


def _index_joins(order: list[str], joins: list[dict]) -> dict[tuple[str, str], dict]:
    adjacent = set(zip(order, order[1:]))
    indexed = {}
    for join in joins:
        pair = (join["from_page"], join["to_page"])
        if pair not in adjacent:
            raise ValueError(
                f"Reconstruction join {pair[0]!r} → {pair[1]!r} "
                "does not connect adjacent pages"
            )
        if pair in indexed:
            raise ValueError(
                f"Reconstruction repeats the join {pair[0]!r} → {pair[1]!r}"
            )
        indexed[pair] = join
    return indexed


def _page_block(page: dict) -> str:
    flags = page["translation"].get("flags", {})
    return (
        f"[{page['page_id']}] flags={json.dumps(flags)}\n"
        f"ORIGINAL:\n{page['original']['text']}\n"
        f"TRANSLATION:\n{page['translation']['text']}"
    )


def _assemble(span: list[str], by_id: dict, joins: dict, side: str) -> str:
    if not span:
        return ""

    parts = [by_id[span[0]][side]["text"].strip()]
    for previous_id, page_id in zip(span, span[1:]):
        text = by_id[page_id][side]["text"].strip()
        kind = joins.get((previous_id, page_id), {}).get("kind")
        if kind == "hyphenation_repair":
            for index in range(len(parts) - 1, -1, -1):
                parts[index] = parts[index].rstrip("-¬")
                if parts[index]:
                    break
            glue = ""
        elif kind in _FLOW_JOINS:
            glue = " "
        else:
            glue = "\n\n"
        parts.extend((glue, text))
    return "".join(parts)


register(Reconstruct())
