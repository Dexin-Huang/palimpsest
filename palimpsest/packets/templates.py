from __future__ import annotations


def _witness_headings(page_unit: str) -> list[str]:
    if page_unit == "spread":
        return ["Reading Unit 1", "Reading Unit 2", "Layout Notes"]
    return ["Main Witness", "Layout Notes"]


def _translation_headings(page_unit: str) -> list[str]:
    if page_unit == "spread":
        return [
            "Reading Unit 1: [English Header]",
            "Reading Unit 2: [English Header]",
            "Translation Notes",
            "Interpretive Restraint",
        ]
    return [
        "Main Witness: [English Header]",
        "Translation Notes",
        "Interpretive Restraint",
    ]


def packet_heading_contract(kind: str, *, page_unit: str) -> list[str]:
    if kind == "witness":
        return _witness_headings(page_unit)
    if kind == "notes":
        return [
            "Layout",
            "Text Structure",
            "Citations And Allusions",
            "Marginalia And Non-Main Text",
            "Uncertainty Markers",
        ]
    if kind == "translation":
        return _translation_headings(page_unit)
    if kind == "interpretation":
        return [
            "What This Page Is Doing",
            "Direct Evidence",
            "Probable Inference",
            "Connection to Adjacent Pages",
            "Interpretive Restraint",
        ]
    if kind == "terms":
        return [
            "People And Beings",
            "Works And Texts",
            "Places And Institutions",
            "Technical Terms",
        ]
    if kind == "questions":
        return [
            "Witness Uncertainties",
            "Cross-Page Checks",
            "Research Follow-Ups",
        ]
    raise ValueError(f"Unsupported packet template kind: {kind}")


def packet_markdown_template(kind: str, *, page_id: str, page_unit: str) -> str:
    if kind == "witness":
        headings = _witness_headings(page_unit)
        body = [
            f"# Witness: {page_id}",
            "",
            f"## {headings[0]}",
            "**Header**:",
            "**Page Number**:",
            "**Marginalia** (script, position):",
            "```",
            "```",
            "**Main Text**",
            "",
        ]
        if page_unit == "spread":
            body.extend(
                [
                    f"## {headings[1]}",
                    "**Header**:",
                    "**Page Number**:",
                    "**Marginalia** (script, position):",
                    "```",
                    "```",
                    "**Main Text**",
                    "",
                    f"## {headings[2]}",
                    "",
                ]
            )
        else:
            body.extend(
                [
                    f"## {headings[1]}",
                    "",
                ]
            )
        return "\n".join(body)

    if kind == "notes":
        return "\n".join(
            [
                "# Notes",
                "",
                "## Layout",
                "",
                "## Text Structure",
                "",
                "## Citations And Allusions",
                "",
                "## Marginalia And Non-Main Text",
                "",
                "## Uncertainty Markers",
                "",
            ]
        )

    if kind == "translation":
        headings = _translation_headings(page_unit)
        body = [
            "# Working Translation",
            "",
            f"## {headings[0]}",
            "**Main Text**",
            "",
        ]
        if page_unit == "spread":
            body.extend(
                [
                    f"## {headings[1]}",
                    "**Main Text**",
                    "",
                    f"## {headings[2]}",
                    "",
                    f"## {headings[3]}",
                    "",
                ]
            )
        else:
            body.extend(
                [
                    f"## {headings[1]}",
                    "",
                    f"## {headings[2]}",
                    "",
                ]
            )
        return "\n".join(body)

    if kind == "interpretation":
        return "\n".join(
            [
                f"# Interpretation: {page_id}",
                "",
                "## What This Page Is Doing",
                "",
                "## Direct Evidence",
                "",
                "## Probable Inference",
                "",
                "## Connection to Adjacent Pages",
                "",
                "## Interpretive Restraint",
                "",
            ]
        )

    if kind == "terms":
        return "\n".join(
            [
                "# Names And Terms",
                "",
                "## People And Beings",
                "",
                "## Works And Texts",
                "",
                "## Places And Institutions",
                "",
                "## Technical Terms",
                "",
            ]
        )

    if kind == "questions":
        return "\n".join(
            [
                "# Open Questions",
                "",
                "## Witness Uncertainties",
                "",
                "## Cross-Page Checks",
                "",
                "## Research Follow-Ups",
                "",
            ]
        )

    raise ValueError(f"Unsupported packet template kind: {kind}")


def packet_heading_contract_block(*, page_unit: str) -> str:
    ordered_kinds = [
        ("witness.md", "witness"),
        ("notes.md", "notes"),
        ("translation.md", "translation"),
        ("interpretation.md", "interpretation"),
        ("terms.md", "terms"),
        ("questions.md", "questions"),
    ]
    lines: list[str] = []
    for filename, kind in ordered_kinds:
        headings = packet_heading_contract(kind, page_unit=page_unit)
        lines.append(f"{filename}:")
        for heading in headings:
            prefix = "###" if kind == "interpretation" and heading in {"Direct Evidence", "Probable Inference"} else "##"
            lines.append(f"- {prefix} {heading}")
        lines.append("")
    return "\n".join(lines).strip()


def packet_format_contract_block(*, page_unit: str) -> str:
    witness_heading = _witness_headings(page_unit)[0]
    translation_heading = _translation_headings(page_unit)[0]
    witness_lines = [
        "witness.md internal shape:",
        f"- ## {witness_heading}",
        "- **Header**: visible header text if present",
        "- **Page Number**: visible page number if present",
        "- **Marginalia** (script, position): then a fenced code block if marginal text exists",
        "- **Main Text** followed by diplomatic witness only, one visual line or sentence per line",
    ]
    if page_unit == "spread":
        witness_lines.append(f"- ## {_witness_headings(page_unit)[1]} uses the same shape if a second region exists")

    translation_lines = [
        "",
        "translation.md internal shape:",
        f"- ## {translation_heading}",
        "- The part before the colon must match the witness unit title exactly",
        "- **Main Text** followed by close translation paragraphs only",
        "- Keep commentary out of the translation units",
    ]

    interpretation_lines = [
        "",
        "interpretation.md internal shape:",
        "- Use top-level ## sections only",
        "- Keep these sections distinct: What This Page Is Doing, Direct Evidence, Probable Inference, Connection to Adjacent Pages, Interpretive Restraint",
        "",
        "terms.md item shape:",
        "- Use list items like: - **上帝** (Shangdi): visible divine name on the page",
        "",
        "notes.md and questions.md:",
        "- Use concise bullet items under the fixed section headings",
    ]

    return "\n".join(witness_lines + translation_lines + interpretation_lines).strip()
