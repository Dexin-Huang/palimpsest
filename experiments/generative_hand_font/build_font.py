"""Assemble authentic P.3477 specimens and generated completions into a TTF."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import cv2
import numpy as np
from PIL import Image


HERE = Path(__file__).parent
ROOT = HERE.parents[1]
OUT = HERE / "out"
BENCHMARK_PATH = OUT / "benchmark.json"
ADAPTATION_PATH = OUT / "adaptation.json"
GENERATION_PATH = OUT / "generation.json"
FONT_PATH = OUT / "P3477-Generated.ttf"
PROVENANCE_PATH = OUT / "font_provenance.json"
UPM = 1000
CANVAS = 128
GLYPH_BOX = (70, -40, 930, 880)
THRESHOLD = 200
UPSCALE = 4
MINIMUM_CONTOUR_AREA = 40


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def resolve(path: str) -> Path:
    candidate = Path(path)
    return candidate if candidate.is_absolute() else ROOT / candidate


def glyph_name(character: str) -> str:
    codepoint = ord(character)
    return f"uni{codepoint:04X}" if codepoint <= 0xFFFF else f"u{codepoint:05X}"


def glyph_contours(image: np.ndarray) -> list[np.ndarray]:
    ink = np.uint8(image < THRESHOLD) * 255
    enlarged = cv2.resize(
        ink,
        (CANVAS * UPSCALE, CANVAS * UPSCALE),
        interpolation=cv2.INTER_NEAREST,
    )
    enlarged = cv2.morphologyEx(
        enlarged, cv2.MORPH_CLOSE, np.ones((3, 3), np.uint8)
    )
    contours, _ = cv2.findContours(
        enlarged, cv2.RETR_CCOMP, cv2.CHAIN_APPROX_TC89_KCOS
    )
    simplified = []
    for contour in contours:
        if cv2.contourArea(contour) < MINIMUM_CONTOUR_AREA:
            continue
        epsilon = 0.004 * cv2.arcLength(contour, True)
        simplified.append(cv2.approxPolyDP(contour, epsilon, True).reshape(-1, 2))
    return simplified


def outline_glyph(image: np.ndarray):
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    x0, y0, x1, y1 = GLYPH_BOX
    width = CANVAS * UPSCALE
    pen = TTGlyphPen(None)
    for contour in glyph_contours(image):
        for index, (pixel_x, pixel_y) in enumerate(contour):
            font_x = x0 + float(pixel_x) / width * (x1 - x0)
            font_y = y1 - float(pixel_y) / width * (y1 - y0)
            point = (round(font_x), round(font_y))
            if index == 0:
                pen.moveTo(point)
            else:
                pen.lineTo(point)
        pen.closePath()
    return pen.glyph()


def load_image(path: Path) -> np.ndarray:
    return np.asarray(
        Image.open(path).convert("L").resize((CANVAS, CANVAS), Image.Resampling.LANCZOS)
    )


def authentic_records(benchmark: dict, adaptation: dict) -> dict[str, dict]:
    budget = len(adaptation["summaries"]["p3477"]["crop_ids"])
    return {
        record["character"]: record
        for record in benchmark["specimen_budgets"][str(budget)]
    }


def generated_records(generation: dict) -> dict[str, dict]:
    return {
        record["character"]: record
        for record in generation["outputs"]["p3477_calibrated"]
    }


def build_sources(
    benchmark: dict, adaptation: dict, generation: dict
) -> tuple[dict[str, np.ndarray], dict[str, dict]]:
    authentic = authentic_records(benchmark, adaptation)
    generated = generated_records(generation)
    characters = sorted(set(benchmark["output_repertoire"]) | set(authentic))
    images = {}
    provenance = {}
    for character in characters:
        if character in authentic:
            record = authentic[character]
            path = resolve(record["crop_path"])
            source = {
                "kind": "authentic",
                "source_crop_id": record["crop_id"],
                "source_crop_sha256": record["crop_sha256"],
                "source_page": record["page_id"],
                "source_bbox": record["bbox"],
                "label_status": record["label_status"],
            }
        else:
            record = generated[character]
            path = resolve(record["path"])
            source = {
                "kind": "generated",
                "generated_image_sha256": record["sha256"],
                "adapter_sha256": adaptation["adapters"]["p3477"]["sha256"],
                "canonical_content_font_sha256": benchmark["canonical_content"][
                    "font_sha256"
                ],
                "documentary_evidence": False,
            }
        if sha256(path) not in {
            record.get("crop_sha256"),
            record.get("sha256"),
        }:
            raise RuntimeError(f"Glyph source hash mismatch: {path}")
        image = load_image(path)
        if not glyph_contours(image):
            raise RuntimeError(f"Glyph has no usable contour: {character} ({path})")
        images[character] = image
        provenance[character] = {
            "character": character,
            "codepoint": f"U+{ord(character):04X}",
            "glyph_name": glyph_name(character),
            "source_path": path.relative_to(ROOT).as_posix(),
            **source,
        }
    return images, provenance


def build_font(images: dict[str, np.ndarray], path: Path) -> None:
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    characters = sorted(images)
    order = [".notdef", "space"] + [glyph_name(character) for character in characters]
    builder = FontBuilder(UPM, isTTF=True)
    builder.setupGlyphOrder(order)
    builder.setupCharacterMap(
        {32: "space", **{ord(character): glyph_name(character) for character in characters}}
    )
    empty = TTGlyphPen(None).glyph()
    glyphs = {".notdef": empty, "space": empty}
    metrics = {".notdef": (UPM, 0), "space": (500, 0)}
    for character in characters:
        name = glyph_name(character)
        glyphs[name] = outline_glyph(images[character])
        metrics[name] = (UPM, 0)
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics(metrics)
    builder.setupHorizontalHeader(ascent=880, descent=-120)
    builder.setupOS2(
        sTypoAscender=880,
        sTypoDescender=-120,
        usWinAscent=920,
        usWinDescent=120,
    )
    builder.setupNameTable(
        {
            "familyName": "P3477 Generated",
            "styleName": "Regular",
            "fullName": "P3477 Generated Regular",
            "psName": "P3477Generated-Regular",
            "version": "Version 0.1",
            "uniqueFontIdentifier": "P3477-Generated-v0.1",
            "description": (
                "Experimental P.3477 writer reconstruction. Authentic and "
                "generated glyph provenance is stored beside the font."
            ),
        }
    )
    builder.setupPost()
    builder.save(path)


def main() -> None:
    benchmark = json.loads(BENCHMARK_PATH.read_text(encoding="utf-8"))
    adaptation = json.loads(ADAPTATION_PATH.read_text(encoding="utf-8"))
    generation = json.loads(GENERATION_PATH.read_text(encoding="utf-8"))
    if generation["benchmark_sha256"] != sha256(BENCHMARK_PATH):
        raise RuntimeError("Generation does not match the frozen benchmark")
    if generation["adaptation_sha256"] != sha256(ADAPTATION_PATH):
        raise RuntimeError("Generation does not match the adaptation record")

    images, provenance = build_sources(benchmark, adaptation, generation)
    build_font(images, FONT_PATH)
    authentic_count = sum(item["kind"] == "authentic" for item in provenance.values())
    record = {
        "schema_version": 1,
        "experiment": benchmark["experiment"],
        "font_path": FONT_PATH.relative_to(ROOT).as_posix(),
        "font_sha256": sha256(FONT_PATH),
        "family_name": "P3477 Generated",
        "glyph_count": len(provenance),
        "authentic_glyph_count": authentic_count,
        "generated_glyph_count": len(provenance) - authentic_count,
        "space_codepoint_included": True,
        "generated_pixels_are_documentary_evidence": False,
        "benchmark_sha256": sha256(BENCHMARK_PATH),
        "adaptation_sha256": sha256(ADAPTATION_PATH),
        "generation_sha256": sha256(GENERATION_PATH),
        "glyphs": provenance,
    }
    PROVENANCE_PATH.write_text(
        json.dumps(record, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(
        json.dumps(
            {
                "font": str(FONT_PATH),
                "sha256": record["font_sha256"],
                "glyphs": record["glyph_count"],
                "authentic": record["authentic_glyph_count"],
                "generated": record["generated_glyph_count"],
            },
            ensure_ascii=False,
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
