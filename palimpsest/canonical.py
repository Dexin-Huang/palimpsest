from __future__ import annotations

import json
import re
from datetime import datetime
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

from palimpsest.models import (
    ImageInfo,
    Layout,
    Note,
    Page,
    PageClassification,
    PageReading,
    PageRestorationHints,
    PipelineInfo,
    PreparationInfo,
    PreparationStep,
    PreparedImage,
    ScriptInfo,
    SourceInfo,
    TextLayers,
    Zone,
    ZoneStructure,
)

LINE_NOTE_RE = re.compile(r"^line\s+(\d+)\b", re.IGNORECASE)
LINE_NOTE_RANGE_RE = re.compile(r"^lines?\s+(\d+)\s*(?:-|to)\s*(\d+)\b", re.IGNORECASE)
LINE_NOTE_AND_RE = re.compile(r"^lines?\s+(\d+)\s+and\s+(\d+)\b", re.IGNORECASE)

PAGE_TYPE_MAP = {
    "mixed": "text_page",
    "text_page": "text_page",
    "cover": "cover",
    "blank": "blank",
    "ownership": "ownership",
    "binding": "binding",
    "illustration_only": "illustration_only",
    "index": "index",
    "other": "other",
}

SCRIPT_BY_LANGUAGE = {
    "la": "latin",
    "lat": "latin",
    "en": "latin",
    "es": "latin",
    "it": "latin",
    "fr": "latin",
    "de": "latin",
    "grc": "greek",
    "el": "greek",
    "ar": "arabic",
    "he": "hebrew",
    "zh": "han",
    "lzh": "han",
    "ja": "han",
}

TEXT_LAYER_BY_LANGUAGE = {
    "es": "es_diplomatic",
    "spa": "es_diplomatic",
}
PREFERRED_TEXT_LAYERS = [
    "source_diplomatic",
    "la_diplomatic",
    "es_diplomatic",
    "source_normalized",
    "la_normalized",
    "es_normalized",
]


def _utc_now() -> str:
    return datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8-sig"))


def _infer_doc_dir(transcriptions_dir: Path, image_dir: Optional[Path]) -> Optional[Path]:
    candidates: list[Path] = []
    if image_dir is not None:
        candidates.append(image_dir.parent)
    candidates.append(transcriptions_dir.parent.parent)
    for candidate in candidates:
        if (candidate / "metadata.json").exists() and (candidate / "page_list.json").exists():
            return candidate
    return None


def _load_context(transcriptions_dir: Path, image_dir: Optional[Path]) -> tuple[Optional[Path], dict[str, Any], dict[str, Any], dict[str, Any]]:
    doc_dir = _infer_doc_dir(transcriptions_dir, image_dir)
    metadata: dict[str, Any] = {}
    page_list: dict[str, Any] = {}
    run_meta: dict[str, Any] = {}
    if doc_dir:
        metadata_path = doc_dir / "metadata.json"
        page_list_path = doc_dir / "page_list.json"
        if metadata_path.exists():
            metadata = _read_json(metadata_path)
        if page_list_path.exists():
            page_list = _read_json(page_list_path)
    runs_dir = transcriptions_dir / "_runs"
    if runs_dir.exists():
        run_files = sorted(runs_dir.glob("run_*.json"))
        if run_files:
            run_meta = _read_json(run_files[-1])
    return doc_dir, metadata, page_list, run_meta


def _resolve_image_dir(transcriptions_dir: Path, image_dir: Optional[Path], run_meta: dict[str, Any], doc_dir: Optional[Path]) -> Optional[Path]:
    if image_dir and image_dir.exists():
        return image_dir
    run_image_dir = run_meta.get("image_dir")
    if isinstance(run_image_dir, str):
        candidate = Path(run_image_dir)
        if candidate.exists():
            return candidate
    if doc_dir:
        cleaned = doc_dir / "images_cleaned"
        original = doc_dir / "images"
        if cleaned.exists():
            return cleaned
        if original.exists():
            return original
    return None


def _build_page_map(page_list: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], dict[str, dict[str, Any]]]:
    by_page_id: dict[str, dict[str, Any]] = {}
    by_stem: dict[str, dict[str, Any]] = {}
    for page in page_list.get("pages", []):
        if not isinstance(page, dict):
            continue
        page_id = page.get("page_id")
        if isinstance(page_id, str):
            by_page_id[page_id] = page
        filename = page.get("filename")
        if isinstance(filename, str):
            by_stem[Path(filename).stem] = page
    return by_page_id, by_stem


def _build_image_map(image_dir: Optional[Path]) -> dict[str, Path]:
    if image_dir is None or not image_dir.exists():
        return {}
    return {
        path.stem: path
        for path in image_dir.iterdir()
        if path.is_file()
    }


def _safe_dimensions(path: Optional[Path], width_hint: Optional[int], height_hint: Optional[int]) -> tuple[int, int]:
    if isinstance(width_hint, int) and width_hint > 0 and isinstance(height_hint, int) and height_hint > 0:
        return width_hint, height_hint
    if path and path.exists():
        try:
            from PIL import Image

            with Image.open(path) as img:
                return img.width, img.height
        except Exception:
            pass
    return 1, 1


def _source_name(metadata: dict[str, Any], page_list: dict[str, Any]) -> str:
    explicit = metadata.get("repository") or metadata.get("source_name")
    if isinstance(explicit, str) and explicit.strip():
        return explicit.strip()
    url = page_list.get("manifest_url") or metadata.get("source_url")
    if isinstance(url, str) and url:
        netloc = urlparse(url).netloc.lower()
        if "vatlib" in netloc:
            return "Vatican Library"
        if netloc:
            return netloc
    return "unknown_source"


def _reading_direction(legacy_page: dict[str, Any]) -> str:
    layout = legacy_page.get("layout", {})
    reading_order = ""
    if isinstance(layout, dict):
        reading_order = str(layout.get("reading_order", "")).lower()
    if "top-to-bottom" in reading_order or "ttb" in reading_order:
        return "ttb"
    if "bottom-to-top" in reading_order or "btt" in reading_order:
        return "btt"
    if "right-to-left" in reading_order or "rtl" in reading_order:
        return "rtl"
    return "ltr"


def _normalize_reading_direction(value: Any) -> str:
    raw = str(value or "").strip().lower()
    if not raw:
        return "ltr"
    if raw in {"ltr", "rtl", "ttb", "btt"}:
        return raw
    if raw in {"v-rtl", "vertical-rtl", "vertical_rl", "vertical-rl"}:
        return "ttb"
    if raw in {"v-ltr", "vertical-ltr", "vertical_lr", "vertical-lr"}:
        return "ttb"
    if "top-to-bottom" in raw:
        return "ttb"
    if "bottom-to-top" in raw:
        return "btt"
    if "right-to-left" in raw:
        return "rtl"
    return "ltr"


def _page_type(legacy_page: dict[str, Any], total_lines: int) -> str:
    raw = str(legacy_page.get("page_type") or "").strip().lower()
    if raw in PAGE_TYPE_MAP:
        return PAGE_TYPE_MAP[raw]
    if total_lines == 0:
        return "blank"
    return "text_page"


def _page_has_layer(page_data: dict[str, Any], layer: str) -> bool:
    zones = page_data.get("zones", [])
    if not isinstance(zones, list):
        return False
    for zone in zones:
        if not isinstance(zone, dict):
            continue
        text = zone.get("text", {})
        if isinstance(text, dict) and text.get(layer):
            return True
    return False


def _preferred_layer(metadata: dict[str, Any], page_data: dict[str, Any]) -> str:
    for layer in PREFERRED_TEXT_LAYERS:
        if _page_has_layer(page_data, layer):
            return layer
    language = str(metadata.get("language") or "").lower()
    if TEXT_LAYER_BY_LANGUAGE.get(language) == "es_diplomatic":
        return "es_diplomatic"
    return "source_diplomatic"


def _script_for_language(language: Optional[str]) -> Optional[str]:
    if not language:
        return None
    return SCRIPT_BY_LANGUAGE.get(language.lower())


def _line_refs(note: str) -> list[int]:
    for pattern in (LINE_NOTE_RANGE_RE, LINE_NOTE_AND_RE):
        match = pattern.match(note)
        if match:
            start = int(match.group(1))
            end = int(match.group(2))
            if start <= end:
                return list(range(start, end + 1))
            return list(range(end, start + 1))
    single = LINE_NOTE_RE.match(note)
    if single:
        return [int(single.group(1))]
    return []


def _notes_by_line(page_notes: list[str]) -> tuple[dict[int, list[str]], list[str]]:
    line_notes: dict[int, list[str]] = {}
    leftover: list[str] = []
    for note in page_notes:
        refs = _line_refs(note)
        if not refs:
            leftover.append(note)
            continue
        for ref in refs:
            line_notes.setdefault(ref, []).append(note)
    return line_notes, leftover


def _build_layout(columns: list[dict[str, Any]], total_lines: int) -> Layout:
    column_count = max(1, len(columns))
    return Layout(
        columns=column_count,
        column_gap_norm=0.05 if column_count > 1 else 0.0,
        writing_area_bbox_norm=(0.08, 0.1, 0.84, 0.82),
        line_count_estimate=total_lines if total_lines > 0 else None,
    )


def _line_bbox(column_index: int, line_index: int, column_count: int, line_count: int) -> tuple[float, float, float, float]:
    outer_margin = 0.08
    top_margin = 0.1
    bottom_margin = 0.08
    gap = 0.05 if column_count > 1 else 0.0
    usable_width = 1.0 - outer_margin * 2 - gap * max(0, column_count - 1)
    column_width = usable_width / column_count
    x = outer_margin + column_index * (column_width + gap)
    usable_height = 1.0 - top_margin - bottom_margin
    per_line = usable_height / max(1, line_count)
    y = top_margin + line_index * per_line
    height = min(0.035, per_line * 0.9)
    return (round(x, 4), round(y, 4), round(column_width, 4), round(height, 4))


def _placeholder_zone(note: str) -> Zone:
    return Zone(
        zone_id="z_placeholder",
        type="unknown",
        order=0,
        bbox_norm=(0.08, 0.1, 0.84, 0.82),
        text=TextLayers(source_diplomatic=""),
        structure=ZoneStructure(region_role="paratext", column_index=0, line_index=0),
        notes=[Note(type="page_note", description=note)],
    )


def _build_zones(
    legacy_page: dict[str, Any],
    metadata: dict[str, Any],
) -> tuple[list[Zone], list[str]]:
    columns = legacy_page.get("columns", [])
    if not isinstance(columns, list):
        columns = []
    page_notes_raw = legacy_page.get("page_notes", [])
    page_notes = [str(note) for note in page_notes_raw] if isinstance(page_notes_raw, list) else []
    line_notes, remaining_notes = _notes_by_line(page_notes)
    language = str(metadata.get("language") or "").lower() or None
    script = _script_for_language(language)

    zones: list[Zone] = []
    total_columns = max(1, len(columns))
    total_visible_lines = sum(
        max(
            int(column.get("line_count_visible", 0)) if isinstance(column, dict) and str(column.get("line_count_visible", "")).isdigit() else 0,
            len(column.get("lines", [])) if isinstance(column, dict) and isinstance(column.get("lines"), list) else 0,
        )
        for column in columns
    )
    total_visible_lines = max(total_visible_lines, 1)

    order = 0
    for column_index, column in enumerate(columns):
        if not isinstance(column, dict):
            continue
        lines = column.get("lines", [])
        if not isinstance(lines, list):
            continue
        line_count = max(
            len(lines),
            int(column.get("line_count_visible", 0)) if str(column.get("line_count_visible", "")).isdigit() else 0,
            1,
        )
        for line_index, line in enumerate(lines):
            if not isinstance(line, dict):
                continue
            line_no = line.get("line")
            if not isinstance(line_no, int):
                line_no = line_index + 1
            diplomatic = str(line.get("diplomatic") or "")
            normalized = str(line.get("normalized") or diplomatic)
            zone_notes = [
                Note(type="page_note", description=note_text)
                for note_text in line_notes.get(line_no, [])
            ] or None
            zones.append(
                Zone(
                    zone_id=f"z_c{column_index + 1}_l{line_no:03d}",
                    type="line",
                    order=order,
                    bbox_norm=_line_bbox(column_index, line_index, total_columns, line_count),
                    text=TextLayers(
                        source_diplomatic=diplomatic,
                        source_normalized=normalized,
                    ),
                    script=ScriptInfo(language=language, script=script) if language or script else None,
                    structure=ZoneStructure(
                        region_role="main_text",
                        column_index=column_index,
                        line_index=line_index,
                        section_label=str(column.get("column_id") or f"column_{column_index + 1}"),
                    ),
                    notes=zone_notes,
                )
            )
            order += 1

    if zones:
        return zones, remaining_notes

    note = remaining_notes[0] if remaining_notes else "No text lines emitted by legacy transcription output."
    return [_placeholder_zone(note)], remaining_notes[1:] if remaining_notes else []


def _build_source_info(metadata: dict[str, Any], page_list: dict[str, Any], page_info: dict[str, Any], doc_id: str) -> SourceInfo:
    manifest = page_list.get("manifest_url") or metadata.get("source_url")
    provenance_note = page_info.get("label") or metadata.get("title")
    return SourceInfo(
        name=_source_name(metadata, page_list),
        collection=metadata.get("collection"),
        source_doc_ref=metadata.get("title") or doc_id,
        provenance_note=str(provenance_note) if provenance_note else None,
        iiif_manifest=str(manifest) if manifest else None,
    )


def _build_preparation(
    doc_dir: Optional[Path],
    source_image_path: Path,
    image_path_used: Optional[Path],
) -> Optional[PreparationInfo]:
    if image_path_used is None or source_image_path.resolve() == image_path_used.resolve():
        return None
    base_ref = str(source_image_path.relative_to(doc_dir)).replace("\\", "/") if doc_dir else source_image_path.name
    used_ref = str(image_path_used.relative_to(doc_dir)).replace("\\", "/") if doc_dir else image_path_used.name
    width_px, height_px = _safe_dimensions(image_path_used, None, None)
    return PreparationInfo(
        prepared_images=[
            PreparedImage(
                kind="debleeded",
                path=used_ref,
                width_px=width_px,
                height_px=height_px,
                based_on=base_ref,
            )
        ],
        steps=[
            PreparationStep(
                name="debleed",
                note=f"Transcription used derived image from {image_path_used.parent.name}/",
            )
        ],
        preferred_image_kind="debleeded",
    )


def _build_pipeline_info(legacy_path: Path, run_meta: dict[str, Any]) -> PipelineInfo:
    assumed_components = {"transcription": "palimpsest.transcription.v1"}
    model_versions = {}
    model = run_meta.get("model")
    if isinstance(model, str) and model:
        model_versions["transcription"] = model
    return PipelineInfo(
        assumed_components=assumed_components,
        model_versions=model_versions or None,
        notes=f"Converted from legacy transcription output {legacy_path.name}.",
    )


def _build_restoration_hints(preferred_layer: str) -> PageRestorationHints:
    return PageRestorationHints(
        preferred_text_layer=preferred_layer,
        output_modes=["diplomatic_edition", "normalized_edition", "tei"],
        notes="Derived automatically from legacy transcription JSON.",
    )


def _resolve_source_image_path(
    *,
    page_id: str,
    page_info: dict[str, Any],
    doc_dir: Optional[Path],
    image_path_used: Optional[Path],
) -> Path:
    source_image_path = image_path_used
    inferred_filename = str(page_info.get("filename") or f"{page_id}.jpg")
    if doc_dir:
        original_candidate = doc_dir / "images" / inferred_filename
        if original_candidate.exists():
            source_image_path = original_candidate
        elif source_image_path is None:
            source_image_path = original_candidate
    if source_image_path is None and image_path_used is not None:
        source_image_path = image_path_used
    if source_image_path is None:
        source_image_path = Path(inferred_filename)
    return source_image_path


def _relative_image_path(source_image_path: Path, doc_dir: Optional[Path]) -> str:
    if doc_dir and source_image_path.is_relative_to(doc_dir):
        return str(source_image_path.relative_to(doc_dir)).replace("\\", "/")
    return source_image_path.name


def _pipeline_component_name(schema_version: str) -> str:
    if schema_version == "canonical.page":
        return "palimpsest.transcription.canonical.page"
    return "palimpsest.transcription.v1"


def _default_classification(page_data: dict[str, Any], metadata: dict[str, Any]) -> PageClassification:
    language = str(metadata.get("language") or "").lower() or None
    script = _script_for_language(language)
    total_zones = 0
    total_chars = 0
    for zone in page_data.get("zones", []):
        if not isinstance(zone, dict):
            continue
        total_zones += 1
        text = zone.get("text", {})
        if isinstance(text, dict):
            total_chars += len(str(
                text.get("source_diplomatic")
                or text.get("la_diplomatic")
                or text.get("es_diplomatic")
                or text.get("source_normalized")
                or text.get("la_normalized")
                or text.get("es_normalized")
                or ""
            ))
    page_type = "blank" if total_zones == 0 or total_chars == 0 else "text_page"
    return PageClassification(
        page_type=page_type,
        languages=[language] if language else None,
        scripts=[script] if script else None,
        confidence=0.5,
    )


def _default_layout_from_page_data(page_data: dict[str, Any]) -> Layout:
    zones = page_data.get("zones", [])
    if not isinstance(zones, list):
        zones = []
    column_indexes = set()
    line_count = 0
    for zone in zones:
        if not isinstance(zone, dict):
            continue
        structure = zone.get("structure", {})
        if isinstance(structure, dict):
            column_index = structure.get("column_index")
            if isinstance(column_index, int):
                column_indexes.add(column_index)
        if zone.get("type") in {"line", "rubric", "marginalia", "interlinear", "header", "footer", "page_number", "caption", "diagram_label", "table_cell"}:
            line_count += 1
    columns = max(1, len(column_indexes) or 1)
    return Layout(
        columns=columns,
        column_gap_norm=0.05 if columns > 1 else 0.0,
        writing_area_bbox_norm=(0.08, 0.1, 0.84, 0.82),
        line_count_estimate=line_count or None,
    )


def _normalize_direct_canonical_page(
    *,
    raw_page: dict[str, Any],
    page_id: str,
    doc_id: str,
    metadata: dict[str, Any],
    page_list: dict[str, Any],
    run_meta: dict[str, Any],
    doc_dir: Optional[Path],
    image_path_used: Optional[Path],
) -> Page:
    by_page_id, by_stem = _build_page_map(page_list)
    page_info = by_page_id.get(page_id) or by_stem.get(page_id) or {}
    source_image_path = _resolve_source_image_path(
        page_id=page_id,
        page_info=page_info,
        doc_dir=doc_dir,
        image_path_used=image_path_used,
    )
    width_px, height_px = _safe_dimensions(
        source_image_path,
        page_info.get("width") if isinstance(page_info.get("width"), int) else None,
        page_info.get("height") if isinstance(page_info.get("height"), int) else None,
    )
    preferred_layer = _preferred_layer(metadata, raw_page)
    pipeline = raw_page.get("pipeline")
    pipeline_notes = f"Normalized from direct canonical.page transcription for {page_id}."
    if isinstance(pipeline, dict) and pipeline.get("notes"):
        pipeline_notes = f"{pipeline.get('notes')} | {pipeline_notes}"

    data = dict(raw_page)
    data["schema_version"] = "canonical.page"
    data["created_at"] = _utc_now()
    data["page_id"] = page_id
    data["doc_id"] = doc_id
    data["source"] = _build_source_info(metadata, page_list, page_info, doc_id).model_dump(exclude_none=True)
    data["image"] = ImageInfo(
        path=_relative_image_path(source_image_path, doc_dir),
        width_px=width_px,
        height_px=height_px,
        iiif_url=str(page_info.get("url")) if page_info.get("url") else None,
    ).model_dump(exclude_none=True)
    preparation = _build_preparation(doc_dir, source_image_path, image_path_used)
    if preparation:
        data["preparation"] = preparation.model_dump(exclude_none=True)
    else:
        data.pop("preparation", None)
    data["reading_direction"] = _normalize_reading_direction(data.get("reading_direction"))
    data["coordinate_space"] = "norm01"
    if not data.get("classification"):
        data["classification"] = _default_classification(data, metadata).model_dump(exclude_none=True)
    if not data.get("layout"):
        data["layout"] = _default_layout_from_page_data(data).model_dump(exclude_none=True)
    if not data.get("restoration"):
        data["restoration"] = _build_restoration_hints(preferred_layer).model_dump(exclude_none=True)
    data["pipeline"] = PipelineInfo(
        assumed_components={"transcription": _pipeline_component_name("canonical.page")},
        model_versions={"transcription": run_meta.get("model")} if isinstance(run_meta.get("model"), str) and run_meta.get("model") else None,
        notes=pipeline_notes,
    ).model_dump(exclude_none=True)
    return Page.model_validate(data)


def _build_page(
    *,
    legacy_page: dict[str, Any],
    legacy_path: Path,
    page_id: str,
    doc_id: str,
    metadata: dict[str, Any],
    page_list: dict[str, Any],
    page_info: dict[str, Any],
    run_meta: dict[str, Any],
    doc_dir: Optional[Path],
    image_path_used: Optional[Path],
) -> Page:
    zones, remaining_notes = _build_zones(legacy_page, metadata)
    total_lines = sum(1 for zone in zones if zone.type == "line" and zone.text.primary_text() is not None)
    page_type = _page_type(legacy_page, total_lines)
    language = str(metadata.get("language") or "").lower() or None
    source_image_path = _resolve_source_image_path(
        page_id=page_id,
        page_info=page_info,
        doc_dir=doc_dir,
        image_path_used=image_path_used,
    )

    width_px, height_px = _safe_dimensions(
        source_image_path,
        page_info.get("width") if isinstance(page_info.get("width"), int) else None,
        page_info.get("height") if isinstance(page_info.get("height"), int) else None,
    )

    relative_image_path = _relative_image_path(source_image_path, doc_dir)
    preferred_layer = _preferred_layer(
        metadata,
        {"zones": [zone.model_dump(exclude_none=True) for zone in zones]},
    )
    reading = None
    notable_features = remaining_notes or None
    if notable_features:
        reading = PageReading(notable_features=notable_features, confidence=0.6)

    return Page(
        created_at=_utc_now(),
        page_id=page_id,
        doc_id=doc_id,
        source=_build_source_info(metadata, page_list, page_info, doc_id),
        image=ImageInfo(
            path=relative_image_path,
            width_px=width_px,
            height_px=height_px,
            iiif_url=str(page_info.get("url")) if page_info.get("url") else None,
        ),
        preparation=_build_preparation(doc_dir, source_image_path, image_path_used),
        reading_direction=_reading_direction(legacy_page),
        classification=PageClassification(
            page_type=page_type,
            languages=[language] if language else None,
            scripts=[_script_for_language(language)] if _script_for_language(language) else None,
            confidence=0.7 if legacy_page.get("page_type") else 0.5,
        ),
        layout=_build_layout(legacy_page.get("columns", []) if isinstance(legacy_page.get("columns"), list) else [], total_lines),
        zones=zones,
        reading=reading,
        restoration=_build_restoration_hints(preferred_layer),
        pipeline=_build_pipeline_info(legacy_path, run_meta),
    )


def build_canonical_page(
    *,
    legacy_page: dict[str, Any],
    legacy_path: Path,
    page_id: str,
    doc_id: str,
    metadata: dict[str, Any],
    page_list: dict[str, Any],
    run_meta: dict[str, Any],
    doc_dir: Optional[Path],
    image_path_used: Optional[Path],
) -> Page:
    by_page_id, by_stem = _build_page_map(page_list)
    page_info = by_page_id.get(page_id) or by_stem.get(page_id) or {}
    source = _build_source_info(metadata, page_list, page_info, doc_id)
    page = _build_page(
        legacy_page=legacy_page,
        legacy_path=legacy_path,
        page_id=page_id,
        doc_id=doc_id,
        metadata=metadata,
        page_list=page_list,
        page_info=page_info,
        run_meta=run_meta,
        doc_dir=doc_dir,
        image_path_used=image_path_used,
    )
    return page.model_copy(update={"source": source})


def export_canonical_pages(
    *,
    transcriptions_dir: Path,
    image_dir: Optional[Path] = None,
    out_dir: Optional[Path] = None,
) -> dict[str, Any]:
    transcriptions_dir = transcriptions_dir.resolve()
    doc_dir, metadata, page_list, run_meta = _load_context(transcriptions_dir, image_dir)
    resolved_image_dir = _resolve_image_dir(transcriptions_dir, image_dir, run_meta, doc_dir)
    image_map = _build_image_map(resolved_image_dir)
    final_files = sorted(transcriptions_dir.glob("*_final.json"))
    if not final_files:
        raise ValueError(f"No *_final.json files found in {transcriptions_dir}")

    doc_id = str(metadata.get("doc_id") or (doc_dir.name if doc_dir else transcriptions_dir.parent.name))
    canonical_dir = (out_dir or transcriptions_dir.parent / "canonical_pages").resolve()
    canonical_dir.mkdir(parents=True, exist_ok=True)

    pages_index: list[dict[str, Any]] = []
    by_page_id, by_stem = _build_page_map(page_list)
    for final_path in final_files:
        page_id = final_path.stem[:-6]
        legacy_page = _read_json(final_path)
        page_info = by_page_id.get(page_id) or by_stem.get(page_id) or {}
        filename = page_info.get("filename")
        image_path_used = image_map.get(page_id)
        if image_path_used is None and isinstance(filename, str):
            image_path_used = image_map.get(Path(filename).stem)
        if legacy_page.get("schema_version") == "canonical.page":
            page = _normalize_direct_canonical_page(
                raw_page=legacy_page,
                page_id=page_id,
                doc_id=doc_id,
                metadata=metadata,
                page_list=page_list,
                run_meta=run_meta,
                doc_dir=doc_dir,
                image_path_used=image_path_used,
            )
        else:
            page = build_canonical_page(
                legacy_page=legacy_page,
                legacy_path=final_path,
                page_id=page_id,
                doc_id=doc_id,
                metadata=metadata,
                page_list=page_list,
                run_meta=run_meta,
                doc_dir=doc_dir,
                image_path_used=image_path_used,
            )
        target = canonical_dir / f"{page_id}.json"
        page.save(str(target))
        pages_index.append(
            {
                "page_id": page.page_id,
                "path": str(target),
                "source_transcription": str(final_path),
                "page_type": page.classification.page_type if page.classification else None,
            }
        )

    manifest = {
        "doc_id": doc_id,
        "generated_at": _utc_now(),
        "source_transcriptions_dir": str(transcriptions_dir),
        "image_dir": str(resolved_image_dir) if resolved_image_dir else None,
        "total_pages": len(pages_index),
        "pages": pages_index,
    }
    (canonical_dir / "canonical_manifest.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )
    return manifest
