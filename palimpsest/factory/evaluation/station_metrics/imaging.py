"""Deterministic conformance metrics for image preparation, segmentation, and alignment.

Image scorers consume grayscale-compatible ``pixels`` arrays plus scorer-only masks.
Geometry uses ``[x, y, width, height]`` rectangles in source-image pixels.  Segment
and align scorers consume the production ``regions`` and ``columns[].chars`` shapes.
An absent denominator returns ``None``: blank pages, absent marginalia, no unmatched
characters, and ambiguous order never receive a fabricated zero or perfect score.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping, Sequence
from math import hypot
from pathlib import Path

import cv2
import numpy as np

from ..metrics import Metric, MetricDirection, MetricRegistry
from .read import normalized_character_error_rate


def _bounded(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return max(low, min(high, float(value)))


def _sequence(value: object) -> Sequence[object] | None:
    return (
        value
        if isinstance(value, Sequence) and not isinstance(value, (str, bytes))
        else None
    )


def _pixels(record: Mapping[str, object], key: str) -> np.ndarray | None:
    value = record.get(key)
    if value is None:
        return None
    try:
        pixels = np.asarray(value, dtype=np.float64)
    except (TypeError, ValueError):
        return None
    if pixels.ndim == 3 and pixels.shape[2] in (3, 4):
        pixels = pixels[:, :, :3].mean(axis=2)
    if pixels.ndim != 2 or pixels.size == 0 or not np.isfinite(pixels).all():
        return None
    return pixels


def image_artifact_observation(
    output_path: Path,
    source_path: Path,
    *,
    source_sha256: str,
) -> dict[str, object]:
    """Decode an image artifact and locate its axis-aligned source crop."""
    output = cv2.imread(str(output_path), cv2.IMREAD_COLOR)
    if output is None:
        raise ValueError(f"Evaluation output is not a readable image: {output_path}")
    source = cv2.imread(str(source_path), cv2.IMREAD_COLOR)
    if source is None:
        raise ValueError(f"Evaluation source is not a readable image: {source_path}")
    observation: dict[str, object] = {
        "pixels": output,
        "source_sha256": source_sha256,
    }
    source_bbox = _source_crop_bbox(source, output)
    if source_bbox is not None:
        observation["source_bbox"] = source_bbox
    return observation


def _source_crop_bbox(
    source: np.ndarray,
    output: np.ndarray,
) -> list[int] | None:
    """Locate a re-encoded, unscaled crop within its source image."""
    source_height, source_width = source.shape[:2]
    output_height, output_width = output.shape[:2]
    if output_height > source_height or output_width > source_width:
        return None
    if (output_height, output_width) == (source_height, source_width):
        return [0, 0, output_width, output_height]

    source_gray = cv2.cvtColor(source, cv2.COLOR_BGR2GRAY)
    output_gray = cv2.cvtColor(output, cv2.COLOR_BGR2GRAY)
    scale = min(1.0, 640.0 / max(source_height, source_width))
    scaled_source = cv2.resize(
        source_gray,
        (
            max(1, round(source_width * scale)),
            max(1, round(source_height * scale)),
        ),
        interpolation=cv2.INTER_AREA,
    )
    scaled_output = cv2.resize(
        output_gray,
        (
            max(1, round(output_width * scale)),
            max(1, round(output_height * scale)),
        ),
        interpolation=cv2.INTER_AREA,
    )
    if (
        scaled_output.shape[0] > scaled_source.shape[0]
        or scaled_output.shape[1] > scaled_source.shape[1]
    ):
        return None
    coarse_match = cv2.matchTemplate(
        scaled_source,
        scaled_output,
        cv2.TM_CCOEFF_NORMED,
    )
    _, _, _, coarse_location = cv2.minMaxLoc(coarse_match)
    coarse_x = round(coarse_location[0] / scale)
    coarse_y = round(coarse_location[1] / scale)

    radius = max(4, round(2.0 / scale))
    left = max(0, coarse_x - radius)
    top = max(0, coarse_y - radius)
    right = min(source_width, coarse_x + output_width + radius)
    bottom = min(source_height, coarse_y + output_height + radius)
    if right - left < output_width or bottom - top < output_height:
        return None
    local_match = cv2.matchTemplate(
        source_gray[top:bottom, left:right],
        output_gray,
        cv2.TM_CCOEFF_NORMED,
    )
    _, confidence, _, location = cv2.minMaxLoc(local_match)
    if not np.isfinite(confidence) or confidence < 0.8:
        return None
    return [
        left + location[0],
        top + location[1],
        output_width,
        output_height,
    ]


def _mask(
    gold: Mapping[str, object], key: str, shape: tuple[int, int]
) -> np.ndarray | None:
    value = gold.get(key)
    if value is None:
        return None
    mask = np.asarray(value)
    if mask.shape != shape:
        return None
    return mask.astype(bool)


def _paired_pixels(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> tuple[np.ndarray, np.ndarray] | None:
    candidate = _pixels(output, "pixels")
    source = _pixels(gold, "source_pixels")
    if candidate is None or source is None or candidate.shape != source.shape:
        return None
    return candidate, source


def _rect(value: object) -> tuple[float, float, float, float] | None:
    items = _sequence(value)
    if items is None or len(items) != 4:
        return None
    if any(
        isinstance(item, bool) or not isinstance(item, int | float) for item in items
    ):
        return None
    x, y, width, height = (float(item) for item in items)
    if not all(np.isfinite((x, y, width, height))) or width <= 0 or height <= 0:
        return None
    return x, y, width, height


def _rects(
    record: Mapping[str, object], key: str
) -> list[tuple[float, float, float, float]] | None:
    values = _sequence(record.get(key))
    if values is None:
        return None
    result = [_rect(value) for value in values]
    return (
        None
        if any(rect is None for rect in result)
        else [rect for rect in result if rect]
    )


def _intersection(
    left: tuple[float, float, float, float],
    right: tuple[float, float, float, float],
) -> tuple[float, float, float, float] | None:
    lx, ly, lw, lh = left
    rx, ry, rw, rh = right
    x0, y0 = max(lx, rx), max(ly, ry)
    x1, y1 = min(lx + lw, rx + rw), min(ly + lh, ry + rh)
    return None if x1 <= x0 or y1 <= y0 else (x0, y0, x1 - x0, y1 - y0)


def _union_area(rectangles: Sequence[tuple[float, float, float, float]]) -> float:
    if not rectangles:
        return 0.0
    xs = sorted({x for x, _, width, _ in rectangles for x in (x, x + width)})
    area = 0.0
    for left, right in zip(xs, xs[1:]):
        intervals = sorted(
            (y, y + height)
            for x, y, width, height in rectangles
            if x < right and x + width > left
        )
        covered = 0.0
        if intervals:
            start, end = intervals[0]
            for next_start, next_end in intervals[1:]:
                if next_start > end:
                    covered += end - start
                    start, end = next_start, next_end
                else:
                    end = max(end, next_end)
            covered += end - start
        area += (right - left) * covered
    return area


def _crop_geometry(
    output: Mapping[str, object], gold: Mapping[str, object]
) -> tuple[tuple[float, float, float, float], float, float] | None:
    crop = _rect(output.get("source_bbox"))
    size = _sequence(gold.get("source_size"))
    if crop is None or size is None or len(size) != 2:
        return None
    if any(
        isinstance(item, bool) or not isinstance(item, int | float) for item in size
    ):
        return None
    width, height = (float(item) for item in size)
    x, y, crop_width, crop_height = crop
    if (
        width <= 0
        or height <= 0
        or x < 0
        or y < 0
        or x + crop_width > width
        or y + crop_height > height
    ):
        return None
    return crop, width, height


def _rect_recall(
    output: Mapping[str, object], gold: Mapping[str, object], key: str
) -> float | None:
    geometry = _crop_geometry(output, gold)
    regions = _rects(gold, key)
    if geometry is None or regions is None:
        return None
    crop, _, _ = geometry
    expected = _union_area(regions)
    if expected == 0:
        return None
    retained = _union_area(
        [overlap for region in regions if (overlap := _intersection(region, crop))]
    )
    return _bounded(retained / expected)


def _score_deframe_manuscript_recall(output, gold):
    return _rect_recall(output, gold, "manuscript_regions")


def _score_deframe_edge_recall(output, gold):
    return _rect_recall(output, gold, "edge_annotation_regions")


def _score_deframe_border_precision(output, gold):
    geometry = _crop_geometry(output, gold)
    frames = _rects(gold, "frame_regions")
    if geometry is None or frames is None:
        return None
    crop, width, height = geometry
    removed_area = width * height - crop[2] * crop[3]
    if removed_area <= 0:
        return None
    frame_area = _union_area(frames)
    retained_frame = _union_area(
        [overlap for frame in frames if (overlap := _intersection(frame, crop))]
    )
    return _bounded((frame_area - retained_frame) / removed_area)


def _score_deframe_boundary_error(output, gold):
    geometry = _crop_geometry(output, gold)
    expected = _rect(gold.get("crop_bbox"))
    if geometry is None or expected is None:
        return None
    crop, width, height = geometry
    actual_edges = (crop[0], crop[1], crop[0] + crop[2], crop[1] + crop[3])
    expected_edges = (
        expected[0],
        expected[1],
        expected[0] + expected[2],
        expected[1] + expected[3],
    )
    return _bounded(
        sum(abs(a - b) for a, b in zip(actual_edges, expected_edges))
        / (2 * (width + height))
    )


def _score_deframe_ocr_impact(output, gold):
    candidate = output.get("ocr_text")
    baseline = gold.get("baseline_ocr_text")
    reference = gold.get("ocr_text")
    if not all(isinstance(value, str) for value in (candidate, baseline, reference)):
        return None
    candidate_error = normalized_character_error_rate(candidate, reference)
    baseline_error = normalized_character_error_rate(baseline, reference)
    return _bounded(baseline_error - candidate_error, -1.0, 1.0)


def _score_deframe_traceability(output, gold):
    geometry = _crop_geometry(output, gold)
    expected_sha = gold.get("source_sha256")
    actual_sha = output.get("source_sha256")
    if (
        geometry is None
        or not isinstance(expected_sha, str)
        or not isinstance(actual_sha, str)
    ):
        return None
    return float(actual_sha == expected_sha)


def _score_overlay_residual(output, gold):
    clean = _pixels(gold, "clean_pixels")
    candidate = _pixels(output, "pixels")
    if clean is None or candidate is None or clean.shape != candidate.shape:
        return None
    overlay = _mask(gold, "overlay_mask", clean.shape)
    if overlay is None or not overlay.any():
        return None
    return _bounded(float(np.mean(np.abs(candidate[overlay] - clean[overlay]))) / 255.0)


def _score_glyph_damage(output, gold):
    paired = _paired_pixels(output, gold)
    if paired is None:
        return None
    candidate, source = paired
    protected = _mask(gold, "protected_text_mask", source.shape)
    if protected is None or not protected.any():
        return None
    tolerance = float(gold.get("change_tolerance", 8.0))
    return float(np.mean(np.abs(candidate[protected] - source[protected]) > tolerance))


def _score_false_removal(output, gold):
    paired = _paired_pixels(output, gold)
    if paired is None:
        return None
    candidate, source = paired
    document = _mask(gold, "document_mask", source.shape)
    overlay = _mask(gold, "overlay_mask", source.shape)
    if document is None or overlay is None:
        return None
    eligible = document & ~overlay
    if not eligible.any():
        return None
    tolerance = float(gold.get("change_tolerance", 8.0))
    return float(np.mean(np.abs(candidate[eligible] - source[eligible]) > tolerance))


def _score_protected_ssim(output, gold):
    paired = _paired_pixels(output, gold)
    if paired is None:
        return None
    candidate, source = paired
    protected = _mask(gold, "protected_text_mask", source.shape)
    if protected is None or not protected.any():
        return None
    left, right = candidate[protected], source[protected]
    mean_left, mean_right = float(left.mean()), float(right.mean())
    variance_left, variance_right = float(left.var()), float(right.var())
    covariance = float(np.mean((left - mean_left) * (right - mean_right)))
    c1, c2 = (0.01 * 255) ** 2, (0.03 * 255) ** 2
    score = ((2 * mean_left * mean_right + c1) * (2 * covariance + c2)) / (
        (mean_left**2 + mean_right**2 + c1) * (variance_left + variance_right + c2)
    )
    return _bounded(score)


def _score_invented_strokes(output, gold):
    candidate = _pixels(output, "pixels")
    clean = _pixels(gold, "clean_pixels")
    if candidate is None or clean is None or candidate.shape != clean.shape:
        return None
    document = _mask(gold, "document_mask", clean.shape)
    overlay = _mask(gold, "overlay_mask", clean.shape)
    if document is None or overlay is None:
        return None
    eligible = (
        document & ~overlay & (clean >= float(gold.get("background_threshold", 220.0)))
    )
    if not eligible.any():
        return None
    tolerance = float(gold.get("change_tolerance", 8.0))
    return float(np.mean(candidate[eligible] < clean[eligible] - tolerance))


def _score_flatten_local_separation(output, gold):
    candidate = _pixels(output, "pixels")
    if candidate is None:
        return None
    text = _mask(gold, "text_mask", candidate.shape)
    background = _mask(gold, "background_mask", candidate.shape)
    if text is None or background is None or not text.any() or not background.any():
        return None
    return _bounded(
        (float(candidate[background].mean()) - float(candidate[text].mean())) / 255.0
    )


def _score_background_uniformity(output, gold):
    candidate = _pixels(output, "pixels")
    if candidate is None:
        return None
    background = _mask(gold, "background_mask", candidate.shape)
    if background is None or not background.any():
        return None
    return _bounded(1.0 - float(candidate[background].std()) / 127.5)


def _score_faint_stroke_retention(output, gold):
    paired = _paired_pixels(output, gold)
    if paired is None:
        return None
    candidate, source = paired
    faint = _mask(gold, "faint_stroke_mask", source.shape)
    background = _mask(gold, "background_mask", source.shape)
    if faint is None or background is None or not faint.any() or not background.any():
        return None
    source_separation = float(source[background].mean() - source[faint].mean())
    if source_separation <= 0:
        return None
    candidate_separation = float(candidate[background].mean() - candidate[faint].mean())
    return _bounded(candidate_separation / source_separation)


def _score_clipping_rate(output, gold):
    paired = _paired_pixels(output, gold)
    if paired is None:
        return None
    candidate, source = paired
    content = _mask(gold, "content_mask", source.shape)
    if content is None or not content.any():
        return None
    source_interior = content & (source > 0.5) & (source < 254.5)
    if not source_interior.any():
        return None
    return float(
        np.mean(
            (candidate[source_interior] <= 0.5) | (candidate[source_interior] >= 254.5)
        )
    )


def _score_flatten_geometry(output, gold):
    paired = _paired_pixels(output, gold)
    return (
        None
        if _pixels(output, "pixels") is None or _pixels(gold, "source_pixels") is None
        else float(paired is not None)
    )


def _score_flatten_provenance(output, gold):
    actual, expected = output.get("source_sha256"), gold.get("source_sha256")
    if not isinstance(actual, str) or not isinstance(expected, str):
        return None
    return float(actual == expected)


def _regions(record: Mapping[str, object]) -> list[Mapping[str, object]] | None:
    values = _sequence(record.get("regions"))
    if values is None or any(not isinstance(region, Mapping) for region in values):
        return None
    return list(values)  # type: ignore[arg-type]


def _iou(
    left: tuple[float, float, float, float], right: tuple[float, float, float, float]
) -> float:
    overlap = _intersection(left, right)
    intersection = 0.0 if overlap is None else overlap[2] * overlap[3]
    union = left[2] * left[3] + right[2] * right[3] - intersection
    return 0.0 if union <= 0 else intersection / union


def _region_matches(output, gold):
    predicted, expected = _regions(output), _regions(gold)
    if predicted is None or expected is None:
        return None
    predicted_boxes = [_rect(region.get("bbox")) for region in predicted]
    expected_boxes = [_rect(region.get("bbox")) for region in expected]
    if any(box is None for box in (*predicted_boxes, *expected_boxes)):
        return None
    threshold = gold.get("match_iou_threshold", 0.5)
    if (
        isinstance(threshold, bool)
        or not isinstance(threshold, int | float)
        or not 0 <= threshold <= 1
    ):
        return None
    choices = sorted(
        (
            (_iou(predicted_box, expected_box), predicted_index, expected_index)
            for predicted_index, predicted_box in enumerate(predicted_boxes)
            for expected_index, expected_box in enumerate(expected_boxes)
        ),
        key=lambda item: (-item[0], item[1], item[2]),
    )
    matched_predicted: set[int] = set()
    matched_expected: set[int] = set()
    matches: list[tuple[int, int, float]] = []
    for score, predicted_index, expected_index in choices:
        if score < threshold:
            break
        if (
            predicted_index not in matched_predicted
            and expected_index not in matched_expected
        ):
            matched_predicted.add(predicted_index)
            matched_expected.add(expected_index)
            matches.append((predicted_index, expected_index, score))
    return predicted, expected, predicted_boxes, expected_boxes, matches


def _score_region_precision(output, gold):
    data = _region_matches(output, gold)
    if data is None:
        return None
    predicted, _, _, _, matches = data
    return None if not predicted else len(matches) / len(predicted)


def _score_region_recall(output, gold):
    data = _region_matches(output, gold)
    if data is None:
        return None
    _, expected, _, _, matches = data
    return None if not expected else len(matches) / len(expected)


def _score_region_iou(output, gold):
    data = _region_matches(output, gold)
    if data is None:
        return None
    _, expected, _, _, matches = data
    if not expected or not matches:
        return None if not expected else 0.0
    return sum(score for _, _, score in matches) / len(expected)


def _score_region_intersection(output, gold):
    data = _region_matches(output, gold)
    if data is None:
        return None
    _, expected, predicted_boxes, expected_boxes, _ = data
    if not expected:
        return None
    intersections = [
        overlap
        for expected_box in expected_boxes
        for predicted_box in predicted_boxes
        if (overlap := _intersection(expected_box, predicted_box))
    ]
    expected_area = _union_area(expected_boxes)
    return (
        _bounded(_union_area(intersections) / expected_area) if expected_area else None
    )


def _kind_recall(output, gold, kinds: set[str] | None = None, protected: bool = False):
    data = _region_matches(output, gold)
    if data is None:
        return None
    _, expected, _, _, matches = data
    eligible = {
        index
        for index, region in enumerate(expected)
        if (kinds is None or region.get("kind") in kinds)
        and (not protected or region.get("protected") is True)
    }
    if not eligible:
        return None
    recovered = {expected_index for _, expected_index, _ in matches}
    return len(eligible & recovered) / len(eligible)


def _score_text_region_recall(output, gold):
    return _kind_recall(output, gold, {"text", "body", "marginalia"})


def _score_marginalia_recall(output, gold):
    return _kind_recall(output, gold, {"marginalia"})


def _score_protected_completeness(output, gold):
    return _kind_recall(output, gold, protected=True)


def _score_reading_order(output, gold):
    data = _region_matches(output, gold)
    if data is None:
        return None
    predicted, expected, _, _, matches = data
    ordered = []
    for predicted_index, expected_index, _ in matches:
        predicted_order = predicted[predicted_index].get("reading_order")
        expected_order = expected[expected_index].get("reading_order")
        if not isinstance(predicted_order, int) or isinstance(predicted_order, bool):
            continue
        if not isinstance(expected_order, int) or isinstance(expected_order, bool):
            continue
        ordered.append((predicted_order, expected_order))
    if len(ordered) < 2:
        return None
    concordant = 0
    total = 0
    for index, left in enumerate(ordered):
        for right in ordered[index + 1 :]:
            if left[0] == right[0] or left[1] == right[1]:
                continue
            total += 1
            concordant += (left[0] - right[0]) * (left[1] - right[1]) > 0
    return None if total == 0 else concordant / total


def _score_false_blank(output, gold):
    expected = _regions(gold)
    route = output.get("route")
    if (
        expected is None
        or not expected
        or route not in {"blank", "full_page", "segmented"}
    ):
        return None
    return float(route == "blank")


def _alignment(record: Mapping[str, object]):
    columns = _sequence(record.get("columns"))
    if columns is None:
        return None
    flattened = []
    signatures = []
    for column_index, column in enumerate(columns):
        if not isinstance(column, Mapping):
            return None
        chars = _sequence(column.get("chars"))
        if chars is None or any(not isinstance(char, Mapping) for char in chars):
            return None
        signature = "".join(str(char.get("ch", "")) for char in chars)
        signatures.append(signature)
        for char_index, char in enumerate(chars):
            ch = char.get("ch")
            if not isinstance(ch, str) or not ch:
                return None
            flattened.append((column_index, char_index, ch, char.get("bbox")))
    return flattened, signatures


def _valid_box(value: object, image_size: Sequence[object] | None):
    box = _rect(value)
    if box is None:
        return None
    if image_size is not None and len(image_size) == 2:
        width, height = image_size
        if not isinstance(width, int | float) or not isinstance(height, int | float):
            return None
        if (
            box[0] < 0
            or box[1] < 0
            or box[0] + box[2] > width
            or box[1] + box[3] > height
        ):
            return None
    return box


def _alignment_pairs(output, gold):
    candidate, expected = _alignment(output), _alignment(gold)
    if candidate is None or expected is None:
        return None
    candidate_chars, candidate_signatures = candidate
    expected_chars, expected_signatures = expected
    image_size = _sequence(gold.get("image_size"))
    count = max(len(candidate_chars), len(expected_chars))
    pairs = []
    for index in range(count):
        predicted = candidate_chars[index] if index < len(candidate_chars) else None
        reference = expected_chars[index] if index < len(expected_chars) else None
        predicted_box = (
            None
            if predicted is None or predicted[3] is None
            else _valid_box(predicted[3], image_size)
        )
        expected_box = (
            None
            if reference is None or reference[3] is None
            else _valid_box(reference[3], image_size)
        )
        pairs.append((predicted, reference, predicted_box, expected_box))
    threshold = gold.get("match_iou_threshold", 0.5)
    if (
        isinstance(threshold, bool)
        or not isinstance(threshold, int | float)
        or not 0 <= threshold <= 1
    ):
        return None
    return (
        pairs,
        candidate_signatures,
        expected_signatures,
        float(threshold),
        image_size,
    )


def _alignment_counts(output, gold):
    data = _alignment_pairs(output, gold)
    if data is None:
        return None
    pairs, _, _, threshold, _ = data
    predicted_count = expected_count = true_positive = 0
    for predicted, expected, predicted_box, expected_box in pairs:
        if predicted is not None and predicted[3] is not None:
            predicted_count += 1
        if expected is not None and expected[3] is not None:
            expected_count += 1
        if (
            predicted is not None
            and expected is not None
            and predicted[2] == expected[2]
            and predicted_box is not None
            and expected_box is not None
            and _iou(predicted_box, expected_box) >= threshold
        ):
            true_positive += 1
    return predicted_count, expected_count, true_positive, data


def _score_box_precision(output, gold):
    counts = _alignment_counts(output, gold)
    if counts is None:
        return None
    predicted, _, true_positive, _ = counts
    return None if predicted == 0 else true_positive / predicted


def _score_box_recall(output, gold):
    counts = _alignment_counts(output, gold)
    if counts is None:
        return None
    _, expected, true_positive, _ = counts
    return None if expected == 0 else true_positive / expected


def _score_coordinate_error(output, gold):
    counts = _alignment_counts(output, gold)
    if counts is None:
        return None
    _, _, _, data = counts
    pairs, _, _, _, image_size = data
    if image_size is None or len(image_size) != 2:
        return None
    width, height = image_size
    if (
        not isinstance(width, int | float)
        or not isinstance(height, int | float)
        or width <= 0
        or height <= 0
    ):
        return None
    distances = []
    for predicted, expected, predicted_box, expected_box in pairs:
        if (
            predicted is None
            or expected is None
            or predicted[2] != expected[2]
            or predicted_box is None
            or expected_box is None
        ):
            continue
        candidate_center = (
            predicted_box[0] + predicted_box[2] / 2,
            predicted_box[1] + predicted_box[3] / 2,
        )
        expected_center = (
            expected_box[0] + expected_box[2] / 2,
            expected_box[1] + expected_box[3] / 2,
        )
        distances.append(
            hypot(
                candidate_center[0] - expected_center[0],
                candidate_center[1] - expected_center[1],
            )
        )
    return (
        None
        if not distances
        else _bounded(
            sum(distances) / len(distances) / hypot(float(width), float(height))
        )
    )


def _score_unmatched_recall(output, gold):
    data = _alignment_pairs(output, gold)
    if data is None:
        return None
    unmatched = [pair for pair in data[0] if pair[1] is not None and pair[1][3] is None]
    if not unmatched:
        return None
    retained = sum(
        pair[0] is not None and pair[0][2] == pair[1][2] and pair[0][3] is None
        for pair in unmatched
    )
    return retained / len(unmatched)


def _score_line_association(output, gold):
    data = _alignment_pairs(output, gold)
    if data is None:
        return None
    eligible = [
        pair
        for pair in data[0]
        if pair[0] is not None
        and pair[1] is not None
        and pair[1][3] is not None
        and pair[0][2] == pair[1][2]
    ]
    if not eligible:
        return None
    return sum(pair[0][0] == pair[1][0] for pair in eligible) / len(eligible)


def _score_false_binding(output, gold):
    counts = _alignment_counts(output, gold)
    if counts is None:
        return None
    predicted, _, true_positive, _ = counts
    return None if predicted == 0 else 1.0 - true_positive / predicted


def _score_fabricated_coordinates(output, gold):
    data = _alignment_pairs(output, gold)
    if data is None:
        return None
    bound = fabricated = 0
    for predicted, expected, predicted_box, _ in data[0]:
        if predicted is None or predicted[3] is None:
            continue
        bound += 1
        if predicted_box is None or expected is None or expected[3] is None:
            fabricated += 1
    return None if bound == 0 else fabricated / bound


def _score_column_order(output, gold):
    data = _alignment_pairs(output, gold)
    if data is None:
        return None
    _, candidate_signatures, expected_signatures, _, _ = data
    counts = Counter(expected_signatures)
    expected_positions = {
        signature: index
        for index, signature in enumerate(expected_signatures)
        if counts[signature] == 1
    }
    observed = [
        (index, expected_positions[signature])
        for index, signature in enumerate(candidate_signatures)
        if signature in expected_positions
    ]
    if len(observed) < 2:
        return None
    concordant = 0
    total = 0
    for index, left in enumerate(observed):
        for right in observed[index + 1 :]:
            total += 1
            concordant += (left[0] - right[0]) * (left[1] - right[1]) > 0
    return concordant / total


def _score_align_provenance(output, gold):
    actual, expected = output.get("image_sha256"), gold.get("image_sha256")
    if not isinstance(actual, str) or not isinstance(expected, str):
        return None
    return float(actual == expected)


def register_imaging_metrics(registry: MetricRegistry) -> None:
    """Register metrics owned by deframe, dewatermark, flatten, segment, and align."""

    maximize = MetricDirection.MAXIMIZE
    minimize = MetricDirection.MINIMIZE
    metrics = (
        Metric(
            "deframe_manuscript_area_recall", maximize, _score_deframe_manuscript_recall
        ),
        Metric(
            "deframe_border_removal_precision",
            maximize,
            _score_deframe_border_precision,
        ),
        Metric("deframe_crop_boundary_error", minimize, _score_deframe_boundary_error),
        Metric("deframe_edge_annotation_recall", maximize, _score_deframe_edge_recall),
        Metric("deframe_downstream_ocr_impact", maximize, _score_deframe_ocr_impact),
        Metric("deframe_geometry_traceability", maximize, _score_deframe_traceability),
        Metric("dewatermark_overlay_residual", minimize, _score_overlay_residual),
        Metric("dewatermark_glyph_damage_rate", minimize, _score_glyph_damage),
        Metric("dewatermark_false_removal_rate", minimize, _score_false_removal),
        Metric("dewatermark_protected_ssim", maximize, _score_protected_ssim),
        Metric("dewatermark_invented_stroke_rate", minimize, _score_invented_strokes),
        Metric("flatten_local_separation", maximize, _score_flatten_local_separation),
        Metric("flatten_background_uniformity", maximize, _score_background_uniformity),
        Metric(
            "flatten_faint_stroke_retention", maximize, _score_faint_stroke_retention
        ),
        Metric("flatten_clipping_rate", minimize, _score_clipping_rate),
        Metric("flatten_geometry_preservation", maximize, _score_flatten_geometry),
        Metric("flatten_source_provenance", maximize, _score_flatten_provenance),
        Metric("segment_region_precision", maximize, _score_region_precision),
        Metric("segment_region_recall", maximize, _score_region_recall),
        Metric("segment_mean_intersection_over_union", maximize, _score_region_iou),
        Metric(
            "segment_region_intersection_recall", maximize, _score_region_intersection
        ),
        Metric(
            "segment_marginalia_recall", maximize, _score_marginalia_recall
        ),
        Metric("segment_reading_order_accuracy", maximize, _score_reading_order),
        Metric(
            "segment_protected_completeness", maximize, _score_protected_completeness
        ),
        Metric("segment_false_blank_page_rate", minimize, _score_false_blank),
        Metric("align_character_box_precision", maximize, _score_box_precision),
        Metric("align_character_box_recall", maximize, _score_box_recall),
        Metric("align_coordinate_error", minimize, _score_coordinate_error),
        Metric("align_unmatched_character_recall", maximize, _score_unmatched_recall),
        Metric("align_line_association_accuracy", maximize, _score_line_association),
        Metric("align_false_binding_rate", minimize, _score_false_binding),
        Metric(
            "align_fabricated_coordinate_rate", minimize, _score_fabricated_coordinates
        ),
        Metric("align_column_order_accuracy", maximize, _score_column_order),
        Metric("align_image_provenance", maximize, _score_align_provenance),
    )
    for metric in metrics:
        registry.register(metric)
