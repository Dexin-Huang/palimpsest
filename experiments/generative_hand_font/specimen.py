"""Render provenance-aware phrase specimens from the generated TTF."""

from __future__ import annotations

import json
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont


HERE = Path(__file__).parent
ROOT = HERE.parents[1]
OUT = HERE / "out"
FONT_PATH = OUT / "P3477-Generated.ttf"
PROVENANCE_PATH = OUT / "font_provenance.json"
SPECIMEN_PATH = OUT / "font_specimen.png"
KAI_PATH = Path("C:/Windows/Fonts/simkai.ttf")
PHRASES = (
    "玄感脉經一卷",
    "三部者謂寸口爲上部",
    "脉之大會手太陰之動也",
    "病在陽陽脉而陰病在陰陰脉而陽",
)


def draw_provenance_line(
    draw: ImageDraw.ImageDraw,
    position: tuple[int, int],
    text: str,
    font: ImageFont.FreeTypeFont,
    glyphs: dict[str, dict],
) -> None:
    x, y = position
    for character in text:
        source = glyphs[character]["kind"]
        color = "#191919" if source == "authentic" else "#9b3f36"
        draw.text((x, y), character, font=font, fill=color)
        x += round(draw.textlength(character, font=font))


def main() -> None:
    provenance = json.loads(PROVENANCE_PATH.read_text(encoding="utf-8"))
    glyphs = provenance["glyphs"]
    phrases = ["".join(character for character in phrase if character in glyphs) for phrase in PHRASES]
    generated = [character for character, item in glyphs.items() if item["kind"] == "generated"]
    phrases.append("".join(generated[:18]))

    hand = ImageFont.truetype(str(FONT_PATH), 84)
    kai = ImageFont.truetype(str(KAI_PATH), 84)
    title = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 30)
    body = ImageFont.truetype("C:/Windows/Fonts/arial.ttf", 17)
    label = ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", 16)
    width = 1500
    row_height = 205
    height = 150 + row_height * len(phrases)
    canvas = Image.new("RGB", (width, height), "#f3f0e8")
    draw = ImageDraw.Draw(canvas)
    draw.text((38, 26), "P.3477 Generated — installable font specimen", font=title, fill="#171717")
    draw.text(
        (38, 70),
        "Black = authentic specimen outline · red = generated writer reconstruction · gray = canonical Kai content",
        font=body,
        fill="#4d4942",
    )
    draw.text(
        (38, 99),
        f"{provenance['glyph_count']} CJK glyphs: {provenance['authentic_glyph_count']} authentic, {provenance['generated_glyph_count']} generated",
        font=body,
        fill="#4d4942",
    )

    for index, phrase in enumerate(phrases):
        y = 145 + index * row_height
        draw.text((38, y), "P3477", font=label, fill="#695f54")
        draw_provenance_line(draw, (145, y - 23), phrase, hand, glyphs)
        draw.text((38, y + 93), "KAI", font=label, fill="#8b877f")
        draw.text((145, y + 70), phrase, font=kai, fill="#9c9992")
        draw.line((38, y + 178, width - 38, y + 178), fill="#d2cdc2", width=1)

    canvas.save(SPECIMEN_PATH, optimize=True)
    print(f"specimen: {SPECIMEN_PATH}")


if __name__ == "__main__":
    main()
