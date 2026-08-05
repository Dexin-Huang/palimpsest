"""Tool-bearing agent-cell transcription, sixth iteration: order reconciliation.

``omp_toolbelt6`` is ``omp_toolbelt5`` plus a deterministic reading-order
reconciliation pass between submission and glyph adjudication. Replaying the
stored v5 outputs against their own geometry evidence measured the failure
class precisely, and it is LAYER INTERLEAVING, not merely column order: gold
reads each column's small-character notes inline in column position, while
the layered draft emits all primary text then all commentary. On the catalog
pages the full second-reader channel re-emitted in geometry order (both
layers interleaved) scores 0.88-0.92 against gold where the layered draft
scores 0.50-0.79; on the healthy control the draft is better (0.97 vs 0.93),
and on the one residual page (mth1000-006) geometry order does not match the
annotation order at all (0.52 either way).

The pass therefore reconciles the COMBINED draft (primary plus commentary)
against ALL readable columns in geometry order, and is conservative and
self-checking:

- it triggers only when the combined draft agrees with the reader channel on
  content (bag overlap) but diverges in sequence - healthy pages and pages
  whose order geometry cannot explain are untouched;
- each geometry column claims its best-matching contiguous draft span
  (stdlib difflib, no model calls); claims resolve by descending match
  quality, strictly non-overlapping;
- claimed spans are re-emitted in geometry order, unclaimed draft segments
  follow in their original relative order, so every submitted character
  survives exactly once - the pass reorders draft content, it never injects
  reader text and never deletes;
- the result is adopted only when its sequence agreement with the reader
  channel improves materially; on adoption the commentary layer is folded
  inline (that is the point) and the commentary field empties.

Reordering runs before glyph adjudication so the adjudication alignment sees
true disagreements instead of order noise.
"""

from __future__ import annotations

import difflib
import hashlib
from collections import Counter
from pathlib import Path

import cv2
import numpy as np

from palimpsest.factory import agent_cell
from palimpsest.factory.core.registry import register
from palimpsest.factory.core.station import Job, StationResult
from palimpsest.factory.stations.transcribe import Transcribe
from palimpsest.factory.stations.transcribe_omp import _extension_source_bytes
from palimpsest.factory.stations.transcribe_omp_toolbelt import _checkpoint_path
from palimpsest.factory.stations.transcribe_omp_toolbelt2 import (
    _read_layered_submission,
    _station_usage_with_second_reader,
)
from palimpsest.factory.stations.transcribe_omp_toolbelt3 import (
    _TASK,
    _TOOLBELT3_EXTENSION_BYTES,
    TRANSCRIPTION_TIMEOUT_SECONDS,
)
from palimpsest.factory.stations.transcribe_omp_toolbelt5 import (
    _adjudicate_glyphs,
    _squeeze,
    _stage_geometry,
)

_REORDER_MIN_BAG_OVERLAP = 0.70
_REORDER_MAX_SEQ_RATIO = 0.75
_REORDER_MIN_BLOCK = 2
_REORDER_MIN_GAIN = 0.10
_REORDER_MIN_FINAL = 0.75


def _bag_overlap(a: str, b: str) -> float:
    matched = sum((Counter(a) & Counter(b)).values())
    return matched / max(len(a), len(b), 1)


def _seq_ratio(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, a, b, autojunk=False).ratio()


def _harvest_blocks(
    draft_ns: str, reader_columns: list[str]
) -> list[tuple[int, int, int, int]]:
    """Matching blocks between the draft and every column: (size, col, col_pos, draft_pos).

    Multiple disjoint blocks per column are the point: an interleaved column's
    draft content lives in separate regions (its primary text and its folded
    note), which no single contiguous window can cover.
    """

    blocks: list[tuple[int, int, int, int]] = []
    for column_index, column_ns in enumerate(reader_columns):
        matcher = difflib.SequenceMatcher(None, draft_ns, column_ns, autojunk=False)
        for block in matcher.get_matching_blocks():
            if block.size >= _REORDER_MIN_BLOCK:
                blocks.append((block.size, column_index, block.b, block.a))
    return blocks


def _reorder_layers(
    columns: list[dict[str, object]],
    primary: str,
    commentary: str,
    two_layer: bool,
) -> tuple[str, str, dict[str, object]]:
    """Re-emit the combined draft in geometry column order when it is scrambled.

    The ``two_layer`` gate and the adoption floor are measured, not designed:
    the toolbelt6-mthv2-dev24 run adopted on five pages, and the three wins
    were exactly the two-layer pages (the interleave class) while both
    casualties were single-layer pages whose annotation order geometry cannot
    explain; the one damaging adoption ended at sequence 0.700 against the
    reader channel while every healthy adoption ended at or above 0.81.
    """

    stats: dict[str, object] = {
        "triggered": False,
        "adopted": False,
        "two_layer": two_layer,
        "bag_overlap": None,
        "seq_ratio_before": None,
        "seq_ratio_after": None,
        "columns_readable": 0,
        "columns_claimed": 0,
        "chars_claimed": 0,
        "chars_leftover": 0,
    }
    if not two_layer:
        return primary, commentary, stats
    reader_columns = [
        _squeeze(str(c["second_reader"]))[0]
        for c in columns
        if isinstance(c["second_reader"], str)
    ]
    reader_columns = [text for text in reader_columns if text]
    stats["columns_readable"] = len(reader_columns)
    combined = primary + ("\n" + commentary if commentary.strip() else "")
    draft_ns, draft_map = _squeeze(combined)
    reader_all = "".join(reader_columns)
    if not draft_ns or not reader_all:
        return primary, commentary, stats

    bag = _bag_overlap(draft_ns, reader_all)
    seq_before = _seq_ratio(draft_ns, reader_all)
    stats["bag_overlap"] = round(bag, 4)
    stats["seq_ratio_before"] = round(seq_before, 4)
    if bag < _REORDER_MIN_BAG_OVERLAP or seq_before > _REORDER_MAX_SEQ_RATIO:
        return primary, commentary, stats
    stats["triggered"] = True

    accepted: list[tuple[int, int, int, int]] = []
    used_draft: list[tuple[int, int]] = []
    used_column: dict[int, list[tuple[int, int]]] = {}
    for size, column_index, column_pos, draft_pos in sorted(
        _harvest_blocks(draft_ns, reader_columns), key=lambda item: -item[0]
    ):
        if any(draft_pos < e and s < draft_pos + size for s, e in used_draft):
            continue
        spans = used_column.setdefault(column_index, [])
        if any(column_pos < e and s < column_pos + size for s, e in spans):
            continue
        used_draft.append((draft_pos, draft_pos + size))
        spans.append((column_pos, column_pos + size))
        accepted.append((column_index, column_pos, draft_pos, size))
    if not accepted:
        return primary, commentary, stats

    claimed_intervals = sorted(used_draft)
    leftover_intervals: list[tuple[int, int]] = []
    cursor = 0
    for start, end in claimed_intervals:
        if cursor < start:
            leftover_intervals.append((cursor, start))
        cursor = end
    if cursor < len(draft_ns):
        leftover_intervals.append((cursor, len(draft_ns)))

    def original_slice(start: int, end: int) -> str:
        return combined[draft_map[start] : draft_map[end - 1] + 1]

    # Emit keys: (column_index, column_pos, tiebreak). Each leftover interval
    # rides with the accepted block that immediately precedes it in draft
    # order, so unmatched characters (reader misses, draft-only text) stay
    # with their column instead of piling at the end. Leftovers before any
    # claimed text lead the page.
    by_draft = sorted(accepted, key=lambda item: item[2])
    pieces: list[tuple[int, int, int, str]] = [
        (column_index, column_pos, 0, original_slice(draft_pos, draft_pos + size))
        for column_index, column_pos, draft_pos, size in accepted
    ]
    claimed_columns = {column_index for column_index, _, _, _ in accepted}
    for start, end in leftover_intervals:
        text = original_slice(start, end)
        preceding = None
        for column_index, column_pos, draft_pos, size in by_draft:
            if draft_pos + size <= start:
                preceding = (column_index, column_pos)
            else:
                break
        if preceding is None:
            pieces.append((-1, 0, start, text))
        else:
            pieces.append((preceding[0], preceding[1], 1 + start, text))
    emitted: list[str] = []
    current_column: int | None = None
    for column_index, _, _, text in sorted(pieces):
        if column_index != current_column:
            emitted.append("\n")
            current_column = column_index
        emitted.append(text)
    candidate = "".join(emitted).strip("\n")
    candidate_ns, _ = _squeeze(candidate)
    seq_after = _seq_ratio(candidate_ns, reader_all)
    stats["seq_ratio_after"] = round(seq_after, 4)
    stats["columns_claimed"] = len(claimed_columns)
    stats["chars_claimed"] = sum(size for _, _, _, size in accepted)
    stats["chars_leftover"] = len(draft_ns) - int(stats["chars_claimed"])
    if seq_after < seq_before + _REORDER_MIN_GAIN or seq_after < _REORDER_MIN_FINAL:
        return primary, commentary, stats
    stats["adopted"] = True
    return candidate, "", stats


class OmpToolbelt6Transcribe(Transcribe):
    """v5 adjudicating reader plus deterministic reading-order reconciliation."""

    variant = "omp_toolbelt6"
    param_keys = frozenset()
    option_keys = frozenset({"extension_source"})
    production_dependencies = (
        "factory/agent_cell.py",
        "factory/stations/transcribe.py",
        "factory/stations/transcribe_omp.py",
        "factory/stations/transcribe_omp_toolbelt.py",
        "factory/stations/transcribe_omp_toolbelt2.py",
        "factory/stations/transcribe_omp_toolbelt3.py",
        "factory/stations/transcribe_omp_toolbelt5.py",
        "factory/stations/align_rfdetr.py",
        "factory/stations/align_rfdetr_runtime.py",
        "factory/gateway/client.py",
        "factory/gateway/omp.py",
    )

    def validate_options(self, options) -> None:
        _extension_source_bytes(options)

    def run(self, job: Job) -> StationResult:
        source_bytes = _extension_source_bytes(job.config.options)
        page_key = hashlib.sha256(str(job.page_id).encode("utf-8")).hexdigest()[:16]
        workspace = agent_cell.stage_workspace(
            self._workspace_root(job) / page_key,
            skill=job.config.prompt.text,
            evidence={},
            images=[job.path_of("page_image")],
        )
        staged_images = sorted((workspace / "images").glob("*"))
        if len(staged_images) != 1:
            raise RuntimeError("toolbelt cell expects exactly one staged page image")
        geometry_summary, adjudication_columns = _stage_geometry(
            workspace, staged_images[0], _checkpoint_path(job)
        )

        extension_dir = workspace / ".omp" / "extensions"
        extension_dir.mkdir(parents=True, exist_ok=True)
        (extension_dir / "00-toolbelt3.ts").write_bytes(_TOOLBELT3_EXTENSION_BYTES)
        (extension_dir / "transcription.ts").write_bytes(source_bytes)

        run = agent_cell.run(
            workspace,
            _TASK,
            model=job.config.model,
            timeout_s=TRANSCRIPTION_TIMEOUT_SECONDS,
            executor="omp",
            tool_names=("read",),
        )
        primary, commentary = _read_layered_submission(workspace)
        primary, commentary, reorder = _reorder_layers(
            adjudication_columns,
            primary,
            commentary,
            bool(geometry_summary["two_layer"]),
        )
        image = cv2.imdecode(
            np.frombuffer(staged_images[0].read_bytes(), dtype=np.uint8),
            cv2.IMREAD_COLOR,
        )
        if image is None:
            raise RuntimeError("cannot decode staged page image for adjudication")
        primary, adjudication = _adjudicate_glyphs(
            image, adjudication_columns, primary, page_key
        )
        page = job.page or {}
        tokens, cost_usd = _station_usage_with_second_reader(
            run.tokens,
            run.cost_usd,
            geometry_summary,
            extra_cost_usd=float(adjudication["cost_usd"]),
        )
        return StationResult(
            payload={
                "doc_id": job.doc_id,
                "page_id": job.page_id,
                "page_seq": page.get("order", 0),
                "canvas_id": page.get("canvas_id", ""),
                "text": primary,
                "commentary": commentary,
                "requested_model": job.config.model,
                "model": job.config.model,
                "finish_reason": "submit_transcription",
                "toolbelt": {
                    **geometry_summary,
                    "reorder": reorder,
                    "adjudication": adjudication,
                },
            },
            tokens_in=tokens,
            cost_usd=cost_usd,
            process_stats=run.process_stats,
        )

    @staticmethod
    def _workspace_root(job: Job) -> Path:
        from palimpsest.factory.workspace.layout import doc_dir

        return (
            doc_dir(job.doc_id, job.library_root) / "runs" / "transcribe_omp_toolbelt6"
        )


register(OmpToolbelt6Transcribe())
