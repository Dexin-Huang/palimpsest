# Lab log

One line per experiment: date, slot, hypothesis, number, verdict.
Losers are deleted from the tree; this file is what remains of them.

- 2026-07-20  m2_exemplars  harvest a scribal exemplar library from
  page_alignment artifacts; gate = leave-one-out purity audit.
  FINDING: purity 0.2% — crops clean, labels wrong. align pairs image
  columns to transcription lines by order, so one missed column shifts
  every later label. M1's true gate is binding purity, not boxed %.
  Next: column-level DTW pairing in glyphs.py, judged by this audit.
