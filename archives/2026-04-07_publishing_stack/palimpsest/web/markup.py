from __future__ import annotations

from dataclasses import dataclass
from html import escape
import re


@dataclass
class MarkdownSection:
    level: int
    title: str
    body: str


@dataclass
class MarkdownDocument:
    title: str | None
    preamble: str
    sections: list[MarkdownSection]


@dataclass
class FolioTemplateSection:
    kind: str
    title: str
    body_html: str
    wide: bool = False


@dataclass
class MarkdownSectionGroup:
    title: str
    level: int
    body: str
    children: list[MarkdownSection]


def _slugify(value: str) -> str:
    lowered = value.lower()
    lowered = re.sub(r"[^a-z0-9]+", "-", lowered)
    lowered = lowered.strip("-")
    return lowered or "section"


def parse_markdown_document(text: str) -> MarkdownDocument:
    lines = text.splitlines()
    title: str | None = None
    preamble_lines: list[str] = []
    sections: list[MarkdownSection] = []
    current_title: str | None = None
    current_level: int | None = None
    current_lines: list[str] = []

    def flush() -> None:
        nonlocal current_title, current_level, current_lines
        if current_title is not None and current_level is not None:
            sections.append(
                MarkdownSection(
                    level=current_level,
                    title=current_title.strip(),
                    body="\n".join(current_lines).strip(),
                )
            )
        current_title = None
        current_level = None
        current_lines = []

    for line in lines:
        match = re.match(r"^(#{1,6})\s+(.*)$", line)
        if match:
            level = len(match.group(1))
            heading = match.group(2).strip()
            if level == 1 and title is None and current_title is None and not preamble_lines and not sections:
                title = heading
                continue
            flush()
            current_title = heading
            current_level = level
            continue
        if current_title is None:
            preamble_lines.append(line)
        else:
            current_lines.append(line)
    flush()

    return MarkdownDocument(
        title=title,
        preamble="\n".join(preamble_lines).strip(),
        sections=sections,
    )


def _render_inline_markdown(text: str) -> str:
    escaped = escape(text)
    escaped = re.sub(r"`([^`]+)`", r"<code>\1</code>", escaped)
    escaped = re.sub(r"\*\*([^*]+)\*\*", r"<strong>\1</strong>", escaped)
    escaped = re.sub(r"\*([^*]+)\*", r"<em>\1</em>", escaped)
    return escaped


def render_markdown_body(text: str, *, preserve_linebreaks: bool = False) -> str:
    lines = text.strip().splitlines()
    html_parts: list[str] = []
    i = 0

    while i < len(lines):
        line = lines[i]
        stripped = line.strip()
        if not stripped:
            i += 1
            continue
        if stripped == "---":
            html_parts.append("<hr>")
            i += 1
            continue
        if stripped.startswith("```"):
            code_lines: list[str] = []
            i += 1
            while i < len(lines) and not lines[i].strip().startswith("```"):
                code_lines.append(lines[i])
                i += 1
            html_parts.append(f"<pre>{escape(chr(10).join(code_lines))}</pre>")
            i += 1
            continue
        heading = re.match(r"^(#{1,6})\s+(.*)$", stripped)
        if heading:
            level = min(len(heading.group(1)) + 1, 6)
            html_parts.append(f"<h{level}>{_render_inline_markdown(heading.group(2).strip())}</h{level}>")
            i += 1
            continue
        if re.match(r"^[-*]\s+", stripped):
            items: list[str] = []
            while i < len(lines):
                item_line = lines[i].strip()
                if not re.match(r"^[-*]\s+", item_line):
                    break
                items.append(f"<li>{_render_inline_markdown(re.sub(r'^[-*]\s+', '', item_line))}</li>")
                i += 1
            html_parts.append("<ul>" + "".join(items) + "</ul>")
            continue
        if re.match(r"^\d+\.\s+", stripped):
            items = []
            while i < len(lines):
                item_line = lines[i].strip()
                if not re.match(r"^\d+\.\s+", item_line):
                    break
                items.append(f"<li>{_render_inline_markdown(re.sub(r'^\d+\.\s+', '', item_line))}</li>")
                i += 1
            html_parts.append("<ol>" + "".join(items) + "</ol>")
            continue

        paragraph_lines = [line]
        i += 1
        while i < len(lines):
            nxt = lines[i]
            nxt_stripped = nxt.strip()
            if not nxt_stripped or nxt_stripped == "---" or nxt_stripped.startswith("```"):
                break
            if re.match(r"^(#{1,6})\s+", nxt_stripped) or re.match(r"^[-*]\s+", nxt_stripped) or re.match(r"^\d+\.\s+", nxt_stripped):
                break
            paragraph_lines.append(nxt)
            i += 1

        rendered = "<br>".join(_render_inline_markdown(item) for item in paragraph_lines) if preserve_linebreaks else _render_inline_markdown(" ".join(item.strip() for item in paragraph_lines))
        html_parts.append(f"<p>{rendered}</p>")

    return "\n".join(html_parts)


def group_document_sections(doc: MarkdownDocument) -> list[MarkdownSectionGroup]:
    if not doc.sections:
        return []
    top_level = min(section.level for section in doc.sections)
    groups: list[MarkdownSectionGroup] = []
    current: MarkdownSectionGroup | None = None
    for section in doc.sections:
        if section.level == top_level:
            current = MarkdownSectionGroup(
                title=section.title,
                level=section.level,
                body=section.body,
                children=[],
            )
            groups.append(current)
            continue
        if current is None:
            current = MarkdownSectionGroup(title="Notes", level=top_level, body="", children=[])
            groups.append(current)
        current.children.append(section)
    return groups


def _render_section_group_body(group: MarkdownSectionGroup, *, preserve_linebreaks: bool = False) -> str:
    parts: list[str] = []
    if group.body.strip():
        parts.append(render_markdown_body(group.body, preserve_linebreaks=preserve_linebreaks))
    for child in group.children:
        child_body = render_markdown_body(child.body, preserve_linebreaks=preserve_linebreaks)
        parts.append(
            "\n".join(
                [
                    '<div class="subsection-card">',
                    f'  <div class="subsection-card-title">{escape(child.title)}</div>',
                    f'  <div class="subsection-card-body">{child_body}</div>',
                    '</div>',
                ]
            )
        )
    return "\n".join(parts)


def render_template_sections(
    sections: list[FolioTemplateSection],
    *,
    article_class: str,
    title_class: str,
) -> str:
    rendered: list[str] = []
    for section in sections:
        wide_class = f" {article_class}-wide" if section.wide else ""
        kind_class = f" {article_class}--{escape(section.kind)}"
        rendered.append(
            "\n".join(
                [
                    f'<article class="{article_class}{wide_class}{kind_class}">',
                    f'  <div class="{title_class}">{escape(section.title)}</div>',
                    f'  <div class="section-card-body">{section.body_html}</div>',
                    '</article>',
                ]
            )
        )
    return "\n".join(rendered)


def _wide_section(kind: str, title: str) -> bool:
    lowered = title.lower()
    return kind in {"interpretation", "notes", "questions"} or lowered in {
        "direct evidence",
        "probable inference",
        "open questions",
    }


def _group_to_template_section(
    group: MarkdownSectionGroup,
    *,
    kind: str,
    preserve_linebreaks: bool = False,
) -> FolioTemplateSection:
    return FolioTemplateSection(
        kind=kind,
        title=group.title,
        body_html=_render_section_group_body(group, preserve_linebreaks=preserve_linebreaks),
        wide=_wide_section(kind, group.title),
    )


def groups_to_template_sections(
    groups: list[MarkdownSectionGroup],
    *,
    kind: str,
    preserve_linebreaks: bool = False,
) -> list[FolioTemplateSection]:
    return [
        _group_to_template_section(group, kind=kind, preserve_linebreaks=preserve_linebreaks)
        for group in groups
    ]
