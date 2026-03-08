# Edition Fonts

Purpose: define the reproducible font policy for packet editions.

Palimpsest uses this order for packet PDF rendering:

1. explicit environment overrides
2. bundled fonts in this folder
3. system fallback fonts

## Environment overrides

- `PALIMPSEST_EDITION_FONT_LATIN`
- `PALIMPSEST_EDITION_FONT_CJK`

Each value may be:
- an absolute path to a font file
- a font family name already installed on the machine

## Bundled font locations

Latin candidates:
- `fonts/latin/Junicode-Regular.ttf`
- `fonts/latin/Junicode.ttf`
- `fonts/latin/NotoSerif-Regular.ttf`
- `fonts/latin/EBGaramond-Regular.ttf`

CJK candidates:
- `fonts/cjk/NotoSerifCJKsc-Regular.otf`
- `fonts/cjk/NotoSerifSC-Regular.otf`
- `fonts/cjk/SourceHanSerifSC-Regular.otf`
- `fonts/cjk/NotoSansCJKsc-Regular.otf`

## Notes

- Do not commit proprietary fonts here.
- Prefer open fonts with good manuscript-edition coverage.
- If nothing is bundled, packet rendering falls back to system fonts.
