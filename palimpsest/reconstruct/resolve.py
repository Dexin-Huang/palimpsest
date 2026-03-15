from __future__ import annotations

import json
import re
from difflib import SequenceMatcher
from pathlib import Path

from google import genai
from google.genai import types
from pydantic import BaseModel

from palimpsest.config import DEFAULT_MODEL_TRIAGE
from palimpsest.contracts import (
    box_cleanup_meta_path,
    box_cleanup_path,
    section_resolution_meta_path,
    section_resolution_path,
    visual_pair_repairs_dir,
)
from palimpsest.models.layout_probe import (
    BoxCleanupDecision,
    LayoutProbe,
    PageBoxCleanup,
    PageSectionResolution,
    PageValidation,
    SectionResolutionAssignment,
)
from palimpsest.reconstruct.artifacts import (
    BoxCleanupArtifact,
    SectionResolutionArtifact,
    VisualPairRepairArtifact,
)
from palimpsest.reconstruct.resolve_support import (
    DEFAULT_LAYOUT_MAX_OUTPUT_TOKENS,
    bbox_overlap_ratio as _bbox_overlap_ratio,
    coerce_json_text as _coerce_json_text,
    draw_pair_overlay as _draw_pair_overlay,
    line_similarity as _line_similarity,
    load_box_cleanup as _load_box_cleanup,
    load_layout_probe as _load_layout_probe,
    load_page_validation as _load_page_validation,
    load_region_reads as _load_region_reads,
    load_section_resolution as _load_section_resolution,
    region_crop_path as _region_crop_path,
    resolve_prompt_text as _resolve_prompt_text,
    response_text as _response_text,
    utc_now as _utc_now,
)


def _section_resolution_payload(layout: LayoutProbe, reads: dict[str, object]) -> list[dict]:
    payload: list[dict] = []
    for region in sorted(layout.regions, key=lambda item: ((item.reading_order or 9999), item.region_id)):
        if region.ignore_for_reconstruction or region.reconstruction_priority == "ignore":
            continue
        read = reads.get(region.region_id)
        if not read or not (read.source_block or read.diplomatic_lines):
            continue
        payload.append(
            {
                "region_id": region.region_id,
                "label": region.label,
                "role": region.role,
                "page_side": region.page_side,
                "column_index": region.column_index,
                "reading_order": region.reading_order,
                "bbox_norm": list(region.bbox_norm),
                "source_block": read.source_block or "\n".join(read.diplomatic_lines),
            }
        )
    return payload


class _SectionResolutionResponse(BaseModel):
    assignments: list[SectionResolutionAssignment]


def _can_resolve_sections_deterministically(sections: list[dict]) -> bool:
    renderable = [section for section in sections if section["role"] != "page_number" and str(section["source_block"]).strip()]
    if not renderable:
        return True
    if any(section["role"] == "header" for section in renderable):
        return False
    counts_by_side: dict[str, int] = {}
    for section in renderable:
        side = section.get("page_side") or "unknown"
        counts_by_side[side] = counts_by_side.get(side, 0) + 1
    if any(count > 2 for count in counts_by_side.values()):
        return False
    allowed_roles = {"main_text", "marginalia", "page_number"}
    return all(section["role"] in allowed_roles for section in sections)


def _deterministic_section_resolution(layout: LayoutProbe, sections: list[dict]) -> PageSectionResolution:
    assignments: list[SectionResolutionAssignment] = []
    for section in sections:
        assignments.append(
            SectionResolutionAssignment(
                region_id=section["region_id"],
                label=section["label"],
                role=section["role"],
                page_side=section["page_side"],
                column_index=section["column_index"],
                render_in_witness=(section["role"] != "page_number"),
                source_block=str(section["source_block"] or ""),
                notes=["Deterministic passthrough for simple page structure."],
            )
        )
    return PageSectionResolution(
        created_at=_utc_now(),
        doc_id=layout.doc_id,
        page_id=layout.page_id,
        assignments=assignments,
    )


def run_section_resolution(
    probe_dir: Path,
    *,
    model: str = DEFAULT_MODEL_TRIAGE,
    prompt_file: Path | None = None,
) -> SectionResolutionArtifact:
    probe_dir = probe_dir.resolve()
    layout = _load_layout_probe(probe_dir)
    reads = {item.region_id: item for item in _load_region_reads(probe_dir)}
    sections = _section_resolution_payload(layout, reads)

    resolution_json_path = section_resolution_path(probe_dir)
    meta_path = section_resolution_meta_path(probe_dir)

    if not sections:
        resolution = PageSectionResolution(
            created_at=_utc_now(),
            doc_id=layout.doc_id,
            page_id=layout.page_id,
            assignments=[],
        )
        resolution_json_path.write_text(resolution.model_dump_json(indent=2), encoding="utf-8")
        meta_path.write_text(
            json.dumps(
                {
                    "generated_at": _utc_now(),
                    "probe_dir": str(probe_dir),
                    "resolution_json_path": str(resolution_json_path),
                    "model": None,
                    "section_count": 0,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return SectionResolutionArtifact(
            probe_dir=probe_dir,
            resolution_json_path=resolution_json_path,
            meta_path=meta_path,
            model=model,
        )

    if _can_resolve_sections_deterministically(sections):
        resolution = _deterministic_section_resolution(layout, sections)
        resolution_json_path.write_text(resolution.model_dump_json(indent=2), encoding="utf-8")
        meta_path.write_text(
            json.dumps(
                {
                    "generated_at": _utc_now(),
                    "probe_dir": str(probe_dir),
                    "resolution_json_path": str(resolution_json_path),
                    "model": None,
                    "section_count": len(sections),
                    "mode": "deterministic",
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return SectionResolutionArtifact(
            probe_dir=probe_dir,
            resolution_json_path=resolution_json_path,
            meta_path=meta_path,
            model=model,
        )

    prompt_text, prompt_path = _resolve_prompt_text(prompt_file, "page_section_resolution")
    prompt_text = (
        prompt_text
        .replace("{PAGE_ID}", layout.page_id)
        .replace("{SECTIONS_JSON}", json.dumps(sections, indent=2, ensure_ascii=False))
    )

    client = genai.Client()
    response = client.models.generate_content(
        model=model,
        contents=[prompt_text],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=_SectionResolutionResponse,
            temperature=0.1,
            max_output_tokens=DEFAULT_LAYOUT_MAX_OUTPUT_TOKENS,
        ),
    )
    text, _ = _response_text(response)
    parsed = _SectionResolutionResponse.model_validate_json(_coerce_json_text(text))
    resolution = PageSectionResolution(
        created_at=_utc_now(),
        doc_id=layout.doc_id,
        page_id=layout.page_id,
        assignments=parsed.assignments,
    )

    resolved_by_region = {item.region_id: item for item in resolution.assignments}
    normalized_assignments: list[SectionResolutionAssignment] = []
    for section in sections:
        candidate = resolved_by_region.get(section["region_id"])
        source_block = candidate.source_block if candidate is not None else section["source_block"]
        render_in_witness = candidate.render_in_witness if candidate is not None else (section["role"] != "page_number")
        notes = list(candidate.notes) if candidate is not None else []

        if section["role"] == "header":
            header_lines = [line for line in str(source_block or "").splitlines() if line.strip()]
            kept_header_lines: list[str] = []
            for line in header_lines:
                compact = re.sub(r"\s+", "", line)
                if not compact:
                    continue
                if len(compact) <= 3:
                    kept_header_lines.append(line)
                else:
                    break
            if kept_header_lines != header_lines:
                notes = notes + ["Header trimmed to compact title lines only."]
            source_block = "\n".join(kept_header_lines)

        if section["role"] == "main_text" and not str(source_block or "").strip():
            source_block = section["source_block"]
            notes = notes + ["Main text restored from source transcription because model returned empty text."]

        normalized_assignments.append(
            SectionResolutionAssignment(
                region_id=section["region_id"],
                label=section["label"],
                role=section["role"],
                page_side=section["page_side"],
                column_index=section["column_index"],
                render_in_witness=render_in_witness,
                source_block=str(source_block or ""),
                notes=notes,
            )
        )

    resolution = PageSectionResolution(
        created_at=resolution.created_at,
        doc_id=resolution.doc_id,
        page_id=resolution.page_id,
        assignments=normalized_assignments,
    )

    resolution_json_path.write_text(resolution.model_dump_json(indent=2), encoding="utf-8")
    meta_path.write_text(
        json.dumps(
            {
                "generated_at": _utc_now(),
                "probe_dir": str(probe_dir),
                "resolution_json_path": str(resolution_json_path),
                "model": model,
                "section_count": len(sections),
                "prompt_path": str(prompt_path),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return SectionResolutionArtifact(
        probe_dir=probe_dir,
        resolution_json_path=resolution_json_path,
        meta_path=meta_path,
        model=model,
    )


def _compact_text_for_match(text: str) -> str:
    return "".join(ch for ch in text if not ch.isspace() and ch not in "[](){}<>.,;:!?\"'`-_=+|/\\")


def _shared_substring_size(a: str, b: str) -> int:
    if not a or not b:
        return 0
    return SequenceMatcher(None, a, b).find_longest_match(0, len(a), 0, len(b)).size


def _box_cleanup_candidates(layout: LayoutProbe, section_resolution: PageSectionResolution) -> list[dict]:
    region_lookup = {region.region_id: region for region in layout.regions}
    assignments = [
        item for item in section_resolution.assignments
        if item.render_in_witness and item.role != "page_number" and str(item.source_block or "").strip()
    ]
    candidates: list[dict] = []
    from itertools import combinations

    for a, b in combinations(assignments, 2):
        region_a = region_lookup.get(a.region_id)
        region_b = region_lookup.get(b.region_id)
        if region_a is None or region_b is None:
            continue
        if region_a.page_side and region_b.page_side and region_a.page_side != region_b.page_side:
            continue

        header_main_neighbor = (
            {a.role, b.role} == {"header", "main_text"}
            and a.page_side == b.page_side
            and a.column_index == b.column_index
        )
        overlap_ratio = _bbox_overlap_ratio(region_a.bbox_norm, region_b.bbox_norm)
        if overlap_ratio < 0.03 and not header_main_neighbor:
            continue

        text_a = str(a.source_block or "").strip()
        text_b = str(b.source_block or "").strip()
        compact_a = _compact_text_for_match(text_a)
        compact_b = _compact_text_for_match(text_b)
        similarity = _line_similarity(compact_a, compact_b) if compact_a and compact_b else 0.0
        shared = _shared_substring_size(compact_a, compact_b)

        if similarity < 0.08 and shared < 2 and not header_main_neighbor:
            continue

        candidates.append(
            {
                "pair_id": f"{a.region_id}:{b.region_id}",
                "region_a": a.region_id,
                "region_b": b.region_id,
                "label_a": a.label,
                "label_b": b.label,
                "role_a": a.role,
                "role_b": b.role,
                "page_side_a": a.page_side,
                "page_side_b": b.page_side,
                "column_index_a": a.column_index,
                "column_index_b": b.column_index,
                "bbox_a": list(region_a.bbox_norm),
                "bbox_b": list(region_b.bbox_norm),
                "overlap_ratio": round(overlap_ratio, 4),
                "text_similarity": round(similarity, 4),
                "shared_substring_size": shared,
                "header_main_neighbor": header_main_neighbor,
                "source_block_a": text_a,
                "source_block_b": text_b,
            }
        )
    return candidates


def _issue_context_for_pair(validation: PageValidation | None, pair_key: set[str]) -> list[dict]:
    if validation is None:
        return []
    matches: list[dict] = []
    for issue in validation.issues:
        issue_regions = set(issue.region_ids)
        if issue_regions == pair_key or pair_key.issubset(issue_regions):
            matches.append(
                {
                    "issue_id": issue.issue_id,
                    "issue_type": issue.issue_type,
                    "severity": issue.severity,
                    "message": issue.message,
                    "evidence": list(issue.evidence),
                }
            )
    return matches


def _trim_header_spill(header_text: str, main_text: str) -> tuple[str, str, list[str]]:
    header_lines = [line for line in str(header_text or "").splitlines() if line.strip()]
    main_lines = [line for line in str(main_text or "").splitlines() if line.strip()]
    notes: list[str] = []

    kept_header: list[str] = []
    for line in header_lines:
        compact = re.sub(r"\s+", "", line)
        if compact and len(compact) <= 3:
            kept_header.append(line)
        else:
            break
    if kept_header:
        header_lines = kept_header

    trimmed = False
    while main_lines:
        compact = re.sub(r"\s+", "", main_lines[0])
        if compact and len(compact) <= 2:
            main_lines.pop(0)
            trimmed = True
            continue
        break
    if trimmed:
        notes.append("Dropped short leading spill line(s) from main text.")

    return "\n".join(header_lines), "\n".join(main_lines), notes


def run_box_cleanup(
    probe_dir: Path,
    *,
    model: str = DEFAULT_MODEL_TRIAGE,
    prompt_file: Path | None = None,
) -> BoxCleanupArtifact:
    probe_dir = probe_dir.resolve()
    layout = _load_layout_probe(probe_dir)
    section_resolution = _load_section_resolution(probe_dir)
    validation = _load_page_validation(probe_dir)
    if section_resolution is None:
        raise FileNotFoundError(f"No section_resolution.json found in {probe_dir}")

    candidates = _box_cleanup_candidates(layout, section_resolution)
    cleanup_json_path = box_cleanup_path(probe_dir)
    meta_path = box_cleanup_meta_path(probe_dir)

    if not candidates:
        cleanup = PageBoxCleanup(
            created_at=_utc_now(),
            doc_id=section_resolution.doc_id,
            page_id=section_resolution.page_id,
            decisions=[],
        )
        cleanup_json_path.write_text(cleanup.model_dump_json(indent=2), encoding="utf-8")
        meta_path.write_text(
            json.dumps(
                {
                    "generated_at": _utc_now(),
                    "probe_dir": str(probe_dir),
                    "cleanup_json_path": str(cleanup_json_path),
                    "model": None,
                    "candidate_count": 0,
                },
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        return BoxCleanupArtifact(
            probe_dir=probe_dir,
            cleanup_json_path=cleanup_json_path,
            meta_path=meta_path,
            model=model,
        )

    prompt_text, prompt_path = _resolve_prompt_text(prompt_file, "page_box_cleanup")
    client = genai.Client()
    decisions: list[BoxCleanupDecision] = []
    current_blocks = {
        assignment.region_id: str(assignment.source_block or "")
        for assignment in section_resolution.assignments
    }
    for candidate in candidates:
        pair_key = {candidate["region_a"], candidate["region_b"]}
        issue_context = _issue_context_for_pair(validation, pair_key)
        candidate["source_block_a"] = current_blocks.get(candidate["region_a"], candidate["source_block_a"])
        candidate["source_block_b"] = current_blocks.get(candidate["region_b"], candidate["source_block_b"])
        if issue_context:
            visual_artifact = run_visual_pair_repair(
                probe_dir,
                region_a=candidate["region_a"],
                region_b=candidate["region_b"],
                model=model,
                source_block_a=candidate["source_block_a"],
                source_block_b=candidate["source_block_b"],
            )
            decision = BoxCleanupDecision.model_validate_json(visual_artifact.decision_json_path.read_text(encoding="utf-8"))
            decisions.append(decision)
            current_blocks[decision.region_a] = decision.cleaned_source_block_a
            current_blocks[decision.region_b] = decision.cleaned_source_block_b
            continue
        final_prompt = (
            prompt_text
            .replace("{PAGE_ID}", layout.page_id)
            .replace("{PAIR_JSON}", json.dumps(candidate, indent=2, ensure_ascii=False))
        )
        response = client.models.generate_content(
            model=model,
            contents=[final_prompt],
            config=types.GenerateContentConfig(
                response_mime_type="application/json",
                temperature=0.1,
                max_output_tokens=DEFAULT_LAYOUT_MAX_OUTPUT_TOKENS,
            ),
        )
        text, _ = _response_text(response)
        payload = json.loads(_coerce_json_text(text))
        payload.setdefault("pair_id", candidate["pair_id"])
        payload.setdefault("region_a", candidate["region_a"])
        payload.setdefault("region_b", candidate["region_b"])
        decision = BoxCleanupDecision.model_validate(payload)
        decisions.append(decision)
        current_blocks[decision.region_a] = decision.cleaned_source_block_a
        current_blocks[decision.region_b] = decision.cleaned_source_block_b

    cleanup = PageBoxCleanup(
        created_at=_utc_now(),
        doc_id=layout.doc_id,
        page_id=layout.page_id,
        decisions=decisions,
    )
    cleanup_json_path.write_text(cleanup.model_dump_json(indent=2), encoding="utf-8")
    meta_path.write_text(
        json.dumps(
            {
                "generated_at": _utc_now(),
                "probe_dir": str(probe_dir),
                "cleanup_json_path": str(cleanup_json_path),
                "model": model,
                "candidate_count": len(candidates),
                "prompt_path": str(prompt_path),
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return BoxCleanupArtifact(
        probe_dir=probe_dir,
        cleanup_json_path=cleanup_json_path,
        meta_path=meta_path,
        model=model,
    )


def run_visual_pair_repair(
    probe_dir: Path,
    *,
    region_a: str,
    region_b: str,
    model: str = DEFAULT_MODEL_TRIAGE,
    prompt_file: Path | None = None,
    source_block_a: str | None = None,
    source_block_b: str | None = None,
) -> VisualPairRepairArtifact:
    probe_dir = probe_dir.resolve()
    layout = _load_layout_probe(probe_dir)
    section_resolution = _load_section_resolution(probe_dir)
    validation = _load_page_validation(probe_dir)
    if section_resolution is None:
        raise FileNotFoundError(f"No section_resolution.json found in {probe_dir}")

    pair_key = {region_a, region_b}
    candidate = None
    for item in _box_cleanup_candidates(layout, section_resolution):
        if {item["region_a"], item["region_b"]} == pair_key:
            candidate = item
            break
    if candidate is None:
        raise ValueError(f"No cleanup candidate found for pair {region_a}, {region_b}")

    issue_context = _issue_context_for_pair(validation, pair_key)
    candidate["issue_context"] = issue_context
    if source_block_a is not None:
        candidate["source_block_a"] = source_block_a
    if source_block_b is not None:
        candidate["source_block_b"] = source_block_b

    prompt_text, prompt_path = _resolve_prompt_text(prompt_file, "page_visual_pair_repair")
    final_prompt = (
        prompt_text
        .replace("{PAGE_ID}", layout.page_id)
        .replace("{PAIR_JSON}", json.dumps(candidate, indent=2, ensure_ascii=False))
    )

    region_lookup = {region.region_id: region for region in layout.regions}
    image_path = Path(layout.image_path).resolve()
    repairs_dir = visual_pair_repairs_dir(probe_dir)
    repairs_dir.mkdir(parents=True, exist_ok=True)
    pair_slug = f"{candidate['region_a']}__{candidate['region_b']}"
    overlay_path = repairs_dir / f"{pair_slug}_overlay.png"
    _draw_pair_overlay(
        image_path,
        bbox_a=region_lookup[candidate["region_a"]].bbox_norm,
        bbox_b=region_lookup[candidate["region_b"]].bbox_norm,
        label_a=f"{candidate['region_a']} {candidate['label_a']}",
        label_b=f"{candidate['region_b']} {candidate['label_b']}",
        out_path=overlay_path,
    )

    crop_a = _region_crop_path(probe_dir, candidate["region_a"])
    crop_b = _region_crop_path(probe_dir, candidate["region_b"])
    if crop_a is None or crop_b is None:
        raise FileNotFoundError(f"Missing crop(s) for pair {candidate['region_a']}, {candidate['region_b']}")

    client = genai.Client()
    response = client.models.generate_content(
        model=model,
        contents=[
            final_prompt,
            types.Part.from_bytes(data=image_path.read_bytes(), mime_type="image/jpeg"),
            types.Part.from_bytes(data=overlay_path.read_bytes(), mime_type="image/png"),
            types.Part.from_bytes(
                data=crop_a.read_bytes(),
                mime_type="image/png" if crop_a.suffix.lower() == ".png" else "image/jpeg",
            ),
            types.Part.from_bytes(
                data=crop_b.read_bytes(),
                mime_type="image/png" if crop_b.suffix.lower() == ".png" else "image/jpeg",
            ),
        ],
        config=types.GenerateContentConfig(
            response_mime_type="application/json",
            temperature=0.1,
            max_output_tokens=DEFAULT_LAYOUT_MAX_OUTPUT_TOKENS,
        ),
    )
    text, finish_reason = _response_text(response)
    payload = json.loads(_coerce_json_text(text))
    payload.setdefault("pair_id", candidate["pair_id"])
    payload.setdefault("region_a", candidate["region_a"])
    payload.setdefault("region_b", candidate["region_b"])
    decision = BoxCleanupDecision.model_validate(payload)

    if any(item["issue_type"] == "header_spill_into_main" for item in issue_context):
        role_a = candidate["role_a"]
        role_b = candidate["role_b"]
        if {role_a, role_b} == {"header", "main_text"}:
            if role_a == "header":
                cleaned_a, cleaned_b, repair_notes = _trim_header_spill(
                    decision.cleaned_source_block_a,
                    decision.cleaned_source_block_b,
                )
                decision = BoxCleanupDecision(
                    pair_id=decision.pair_id,
                    region_a=decision.region_a,
                    region_b=decision.region_b,
                    relation="region_a_owns_overlap" if cleaned_b != decision.cleaned_source_block_b else decision.relation,
                    cleaned_source_block_a=cleaned_a,
                    cleaned_source_block_b=cleaned_b,
                    reason=(decision.reason or "") + (" Post-clean: " + "; ".join(repair_notes) if repair_notes else ""),
                )
            else:
                cleaned_b, cleaned_a, repair_notes = _trim_header_spill(
                    decision.cleaned_source_block_b,
                    decision.cleaned_source_block_a,
                )
                decision = BoxCleanupDecision(
                    pair_id=decision.pair_id,
                    region_a=decision.region_a,
                    region_b=decision.region_b,
                    relation="region_b_owns_overlap" if cleaned_a != decision.cleaned_source_block_a else decision.relation,
                    cleaned_source_block_a=cleaned_a,
                    cleaned_source_block_b=cleaned_b,
                    reason=(decision.reason or "") + (" Post-clean: " + "; ".join(repair_notes) if repair_notes else ""),
                )

    decision_json_path = repairs_dir / f"{pair_slug}.json"
    decision_json_path.write_text(decision.model_dump_json(indent=2), encoding="utf-8")
    meta_path = repairs_dir / f"{pair_slug}_meta.json"
    meta_path.write_text(
        json.dumps(
            {
                "generated_at": _utc_now(),
                "probe_dir": str(probe_dir),
                "decision_json_path": str(decision_json_path),
                "overlay_path": str(overlay_path),
                "image_path": str(image_path),
                "crop_a_path": str(crop_a),
                "crop_b_path": str(crop_b),
                "model": model,
                "prompt_path": str(prompt_path),
                "finish_reason": finish_reason,
            },
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return VisualPairRepairArtifact(
        probe_dir=probe_dir,
        decision_json_path=decision_json_path,
        overlay_path=overlay_path,
        meta_path=meta_path,
        model=model,
    )
