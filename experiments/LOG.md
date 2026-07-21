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

- 2026-07-20  char_inventory/refine  three segmentation fixes on one page
  (page_0001, judged on full-page overlay): valley-split tall cells,
  recover glyph-tall inky gaps, drop margin ghosts. 589 -> 766 (greedy)
  -> 689 kept vs ~607 real chars after tuning split depth 0.35->0.18 and
  recovery gates. Residual: ~13%% over-segmentation, a few wide recovered
  boxes crossing columns. Before/after in out/refine_report.html.  (open)

- 2026-07-20  char_inventory/lasso  boxes -> ink outlines: per-character
  polygons from the components clean_crop already keeps (the box was the
  mask's shadow). 689/689 refined cells outlined on page_0001; polygons
  stored in out/page_0001_lassos.json; boxes-vs-lassos side-by-side in
  out/lasso_report.html. SAM refine staged as next challenger — needs
  torch (not installed; ~2.5GB CPU wheel) + SAM2 weights.  (open)

- 2026-07-20  char_inventory/grabcut  lightweight lasso challenger:
  OpenCV GrabCut, box-prompted, zero new deps. 689 cells in 22s
  (~30ms/char); masks are stroke-tight (systematically tighter than the
  closed-mask incumbent — the "687 materially different" is the closing
  kernel's fat, not error). Defects: some faint chars return background,
  some stain bleed. Next: hybrid init (Otsu mask as GC_PR_FGD seed).
  Big-SAM deferred — the zero-byte rung may be enough.  (open)

- 2026-07-20  char_inventory/cluster  unsupervised: blurred 32x32 density
  features + thresholded leader clustering over 1,948 crops -> 307
  clusters (127 multi-member). Distinctive shapes discovered cleanly
  (pure rows of 之, 脈, 為, 寸 with zero labels); dense complex chars
  clump into mixed attractors — feature resolution is the limit, not the
  approach. Next rungs: higher-res/HOG features, then hierarchical
  re-cluster inside attractors; then cluster-level labeling + document-
  level assignment (cryptogram matching vs transcription).  (open)

- 2026-07-20  separation_sweep  cycle 1: champion swept over 79 folios
  from all 15 zh docs (raw images, deliberately). Median health 0.25 —
  the P.3477-tuned champion collapses in the wild. Failure taxonomy from
  the audit gallery: (1) blank pages segmenting their Vatican watermark
  (worst folio = watermark ring, health 0.01), (2) Gallica frames/color
  charts breaking pitch lock, (3) sparse fragments defeating
  autocorrelation. None are separation defects proper — all are imaging
  prep. Cycle 2: put the factory's imaging bench (blank gate, deframe,
  dewatermark) in front of the sweep and re-measure.  (open)

- 2026-07-20  shape_prior  the language prior: match blobs against all
  20,992 CJK glyphs (Kai font) + Latin negative model, likelihood-ratio
  gate. Raw score: no separation (everything matches something in 21k).
  Margin (CJK minus Latin): watermark kill 8%->40% at 95% manuscript
  retention — real but not clean. Weak labels ~3/16 correct (雨, 在 —
  distinctive silhouettes; dense chars miss but preserve radical
  structure: 陽->漢, 浮->溝). CONVERGENT FINDING: clustering attractors,
  gate overlap, and label misses all share one root cause — the blurred
  32px density feature. One upgrade (gradient/HOG features or tiny
  self-supervised embedding), three scoreboards to prove it on.  (open)

- 2026-07-20  separation2  the integrated approach, validated on the
  frozen 28-folio test set (manifest v1, 16 docs, 4 corpora): imaging
  prep (factory bench + blank gate) + champion geometry + v2 gradient
  features (75.1->79.7% retrieval self-test) + CJK-vs-Latin ratio gate
  (40->62% watermark kill @ 95% retention). Median health per corpus:
  borg_cin 0.31->0.34, estr_or 0.22->0.48, gallica 0.10->0.12, idp
  0.10->0.12, ALL 0.14->0.23. Validation exposed two named defects for
  the next cycle: (1) health metric initially punished the prior gate's
  own rejections (fixed — gate rejections are the pipeline working);
  (2) parchment_frame assumes dark backdrop, so Gallica/IDP light-gray
  frames evade prep — the single biggest remaining lever.  (promoted to
  lab champion; external-SOTA claim awaits M5HisDoc benchmark run)
