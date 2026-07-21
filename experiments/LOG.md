# Lab log

One line per experiment: date, slot, hypothesis, number, verdict.
Losers are deleted from the tree; this file is what remains of them.

- 2026-07-20  m2_exemplars  harvest a scribal exemplar library from
  page_alignment artifacts; gate = leave-one-out purity audit.
  FINDING: purity 0.2% — crops clean, labels wrong. align pairs image
  columns to transcription lines by order, so one missed column shifts
  every later label. M1's true gate is binding purity, not boxed %.
  Next: column-level DTW pairing in glyphs.py, judged by this audit.

- 2026-07-20  align_pairing  DTW column pairing vs order-zip; gate = m2
  purity audit. Visibly better (至 row ~pure) but purity 0.2%→1.0%:
  the audit can't bootstrap at this noise level (mushy class means,
  single-instance distractors). Next: hand-labeled ground truth for
  binding accuracy, then within-column junk gates.  (open)

- 2026-07-20  m2 postprocess  clean_crop: neighbor-ink removal (component
  centroids outside box core dropped) + junk gates (empty/solid/sparse/
  sliver/solid-mass). 705→651 instances, 54 auto-junked; crops visibly
  clean; purity 1.9% — binding, not hygiene, remains the blocker.
  Ground-truth page regenerated with raw+cleaned columns.  (folded into
  m2 candidate)

- 2026-07-20  char_inventory  segmentation-first: emit every geometry
  cell as an unlabeled crop, no transcription involved. 1,959 locations
  (347 junk-gated); pages 0000/0001 clean, page_0002 over-segments
  (glosses/damage). Locations and labels are now decoupled — labeling
  becomes its own swappable step.  (open)
