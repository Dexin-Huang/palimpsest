from __future__ import annotations

from datetime import datetime, timezone
import json
from pathlib import Path
import re

from palimpsest.models import PageAssembly, PageAssemblyUnit, PageValidation, PageValidationIssue
from palimpsest.reconstruct.artifacts import PageValidationArtifact


def _utc_now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _nonempty_lines(text: str) -> list[str]:
    return [line.strip() for line in str(text or "").splitlines() if line.strip()]


def _count_cjk_chars(text: str) -> int:
    return sum(1 for ch in text if "\u3400" <= ch <= "\u9fff" or "\uf900" <= ch <= "\ufaff")


def _find_neighbor_header(units: list[PageAssemblyUnit], target: PageAssemblyUnit) -> PageAssemblyUnit | None:
    for unit in units:
        if unit.role != "header":
            continue
        if unit.page_side != target.page_side:
            continue
        if unit.column_index != target.column_index:
            continue
        return unit
    return None


def run_page_validate(probe_dir: Path) -> PageValidationArtifact:
    probe_dir = probe_dir.resolve()
    assembly_path = probe_dir / "page_assembly.json"
    assembly = PageAssembly.model_validate_json(assembly_path.read_text(encoding="utf-8"))

    issues: list[PageValidationIssue] = []

    for unit in assembly.units:
        lines = _nonempty_lines(unit.source_block or "")

        if unit.role == "main_text":
            header = _find_neighbor_header(assembly.units, unit)
            if header is not None and lines:
                short_prefix: list[str] = []
                for line in lines[:3]:
                    compact = re.sub(r"\s+", "", line)
                    if 0 < len(compact) <= 2:
                        short_prefix.append(compact)
                    else:
                        break
                header_text = "".join(_nonempty_lines(header.source_block or ""))
                if short_prefix:
                    evidence = [f"leading_short_lines={short_prefix}", f"header={header_text}"]
                    issues.append(
                        PageValidationIssue(
                            issue_id=f"{unit.region_id}:header_spill",
                            issue_type="header_spill_into_main",
                            severity="high",
                            region_ids=[header.region_id, unit.region_id],
                            message=f"{unit.label} begins with short header-like spill before the body text.",
                            evidence=evidence,
                        )
                    )

        elif unit.role == "marginalia":
            text = unit.source_block or ""
            cjk_count = _count_cjk_chars(text)
            if cjk_count > 0:
                issues.append(
                    PageValidationIssue(
                        issue_id=f"{unit.region_id}:script_contamination",
                        issue_type="marginalia_script_contamination",
                        severity="high",
                        region_ids=[unit.region_id],
                        message=f"{unit.label} contains CJK spill even though it should be marginalia of a different script family.",
                        evidence=[f"cjk_count={cjk_count}", f"preview={''.join(_nonempty_lines(text)[:4])[:120]}"],
                    )
                )

        elif unit.role == "page_number":
            compact = "".join(lines)
            normalized = compact.lower()
            if len(lines) > 1 or not re.fullmatch(r"[0-9ivxlcdmfrv.\\-]+", normalized):
                issues.append(
                    PageValidationIssue(
                        issue_id=f"{unit.region_id}:page_number_noise",
                        issue_type="page_number_noise",
                        severity="medium",
                        region_ids=[unit.region_id],
                        message=f"{unit.label} contains extra noise beyond a simple folio/page mark.",
                        evidence=[f"lines={lines}", f"compact={compact}"],
                    )
                )

    status = "needs_repair" if issues else "clean"
    score = sum({"low": 1, "medium": 2, "high": 3}[issue.severity] for issue in issues)
    validation = PageValidation(
        created_at=_utc_now(),
        doc_id=assembly.doc_id,
        page_id=assembly.page_id,
        status=status,
        score=score,
        issues=issues,
    )

    validation_json_path = probe_dir / "page_validation.json"
    validation_md_path = probe_dir / "page_validation.md"
    validation_json_path.write_text(validation.model_dump_json(indent=2), encoding="utf-8")

    markdown_lines = [
        f"# Page Validation: {validation.page_id}",
        "",
        f"- Status: `{validation.status}`",
        f"- Score: `{validation.score}`",
        f"- Issue count: `{len(validation.issues)}`",
        "",
    ]
    if not validation.issues:
        markdown_lines.append("No structural issues detected.")
    else:
        for issue in validation.issues:
            markdown_lines.extend(
                [
                    f"## {issue.issue_type}",
                    f"- Severity: `{issue.severity}`",
                    f"- Regions: `{issue.region_ids}`",
                    f"- Message: {issue.message}",
                ]
            )
            if issue.evidence:
                markdown_lines.append("- Evidence:")
                for line in issue.evidence:
                    markdown_lines.append(f"  - {line}")
            markdown_lines.append("")
    validation_md_path.write_text("\n".join(markdown_lines), encoding="utf-8")

    meta_path = probe_dir / "page_validation_meta.json"
    meta = {
        "generated_at": _utc_now(),
        "probe_dir": str(probe_dir),
        "validation_json_path": str(validation_json_path),
        "validation_md_path": str(validation_md_path),
        "assembly_path": str(assembly_path),
        "issue_count": len(validation.issues),
    }
    meta_path.write_text(json.dumps(meta, indent=2, ensure_ascii=False), encoding="utf-8")

    return PageValidationArtifact(
        probe_dir=probe_dir,
        validation_json_path=validation_json_path,
        validation_md_path=validation_md_path,
        meta_path=meta_path,
    )


__all__ = ["run_page_validate"]
