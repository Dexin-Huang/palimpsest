# Glyph Alignment

The `align` station binds diplomatic transcription characters to ink geometry.
It is deterministic image processing: transcription supplies the character
sequence, the cleaned page supplies connected ink, and dynamic programming
aligns the two without another model reading.

## Contract

`align` is a page-grain station:

```text
page_image_clean + page_transcription -> page_alignment
```

A `page_alignment` artifact contains document and page identity, aligned
columns, and count statistics. Each aligned character records:

- the transcribed character;
- an image bounding box when one can be assigned;
- a confidence score;
- the alignment state.

Characters that cannot be bound remain explicit unaligned entries. The station
never invents coordinates to make counts agree.

## Geometry

The implementation in `palimpsest/factory/glyphs.py` follows this sequence:

1. Convert the cleaned page to grayscale and adaptive-threshold it.
2. Remove isolated speckle and collect connected components.
3. Estimate glyph scale from the dominant horizontal autocorrelation period,
   falling back to the upper half of connected-component heights.
4. Close small intra-character gaps conservatively.
5. Detect vertical column bands from a smoothed horizontal ink projection.
6. Split implausibly wide bands at supported interior valleys.
7. Group components into vertical cells inside each column.
8. Sort image columns right-to-left and pair them with transcription lines in
   reading order.
9. Use dynamic programming to match each character to one or more adjacent ink
   cells while allowing damaged characters and noise cells to remain unmatched.

Punctuation and explicit uncertainty markers that carry no independent ink are
excluded from the character sequence before alignment.

## Alignment behavior

The dynamic program minimizes a cost over character and cell indices. Its
operations are:

- match one character to a fused run of nearby cells;
- skip a character when ink is missing or damaged;
- skip a cell when it is noise or unrelated ink.

Bounding-box shape contributes to match cost. Fusing is bounded so a character
cannot absorb an arbitrary span of a column. Unpaired transcription columns are
emitted with unaligned characters rather than discarded.

## Statistics

The artifact reports enough deterministic counts to evaluate alignment quality,
including transcription characters, image columns, aligned characters,
unbound characters, unused ink, and the small-component pool. These values are
production evidence and can be compared across page-cleaning or segmentation
changes without spending model tokens.

## Reader use

Publication keeps the diplomatic text independent of alignment. A reader may
use `page_alignment` to connect a character or column back to page ink, but a
missing alignment never changes the transcription itself. This preserves the
central editorial invariant: geometry can anchor evidence; it cannot rewrite
it.
