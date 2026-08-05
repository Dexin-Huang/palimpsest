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

- 2026-07-20  synthesis  analysis-by-synthesis v0: page_0001 rewritten
  from its own clustered ink (peer instances of each character; no
  fonts, no labels). 246/689 cells peer-rewritten (rest singletons,
  self-copied), rewrite IoU median 0.435, p10 0.280 — the synthetic
  page is a legible facsimile. This is the verification loop: rewrite
  mismatch = localized segmentation/cluster error. Growth path: cross-
  page exemplars raise peer coverage; hand-font/generation covers
  singletons; residual becomes the corpus-wide self-supervised
  objective the perfection loop optimizes. One bug found live: uint8
  overflow (255*255) silently blanked all synthesis — caught because
  the output is an image you look at.  (open)

- 2026-07-20  hand_font  M3 lives: P3477-Hand.ttf, a real TrueType font
  cut from the scribe's ink. 875 labeled crops -> consensus gate
  (cluster coherence x alignment-claim agreement >= 0.6) -> 15 glyphs,
  ~10 authentic (從 尺 者 是 有 下 中 當 render beautifully), ~5
  mislabels that each trace to binding noise (口 renders a 寸; 無 is a
  damaged crop). Vectorization/assembly proven end to end (cv2 contours
  -> fontTools, 4KB TTF, renders via PIL). Coverage scales directly
  with label quality — the labeling ladder is now also the font ladder.
  New dep: fonttools (pure-python).  (open)

- 2026-07-24  light_frame  Lab color distance from the image border as a
  challenger to the dark-backdrop parchment detector, frozen 28-folio
  separation2 comparison. The unbounded arm failed: ALL median health
  0.233->0.149 and IDP 0.120->0.085; preserved in out/v1_unbounded.
  Failure was causal and visible: dark/full-frame scans expanded, while
  small color components could masquerade as a high-health page. The
  guarded arm activates only on light neutral mounts and only when the
  proposed page retains 65-90% of the incumbent frame; all other cases
  fall back exactly. It activated on 8/28 folios: ALL 0.233->0.319,
  estr_or 0.482->0.770, borg_cin median unchanged at 0.343, and
  Gallica/IDP exactly unchanged. Visual audit confirmed real removal of
  Vatican/estr_or white mounts, neighbor pages, and footer furniture;
  the worst folio remained unchanged. The original Gallica/IDP premise
  was refuted—the incumbent already frames those fixtures adequately.
  Next: fold the guarded detector into separation2 prep, then advance
  the zero-byte ladder to mask-seeded GrabCut.  (passed)

- 2026-07-24  character_localization_benchmark  Established the external
  MTHv2 development smoke benchmark: 36 deterministically selected pages
  (12 TKH, 12 MTH1000, 12 MTH1200), 12,332 human character boxes, immutable
  manifest SHA-256
  287ab7dfe0235e5f3a82b7acd82e4eeefbdd58cdc3ff1612a9d5c3112dcafb08.
  The current zero-cost separation2 adapter localizes all 36 pages at
  detection F1 0.600042 and AP50 0.584087. A single-lever challenger removed
  near-continuous horizontal rules after framing: micro F1 0.600414
  (+0.000372) and AP50 0.587534 (+0.003447). The paired per-page F1 delta was
  +0.001597, 95% bootstrap CI [-0.000422, 0.004125]; slice deltas were
  MTH1000 +0.007820, MTH1200 -0.001811, and TKH -0.000220. Verdict:
  inconclusive, prefer baseline. This experiment finds unlabeled character
  geometry only; transcription and recognition are out of scope. Paused
  before the next challenger. On resume: inspect the worst-page overlays,
  classify misses/merges/splits/junk, then change one geometry or gate lever.
  (open)

- 2026-07-24  rf_detr_one_class_tiled_v1  Trained RF-DETR Nano 1.8.3 as a
  one-class historical Chinese character detector. The leak-free training
  manifest contains 90 MTHv2 train pages (30 per corpus) selected after the
  frozen 80-page-per-corpus development reserve. Lossless 512 px tiles with
  96 px overlap produced 2,944 training tiles and 46,044 retained boxes;
  the frozen 36-page development set produced 1,745 validation tiles and
  17,752 boxes. Five epochs on the local RTX 2060 reached tile-validation
  F1 0.9626 and mAP50:95 0.7684. The selected checkpoint SHA-256 is
  17342d6474e0f852febf2bcf73d555155d24172cc72c8a013db3a952d51fa61b;
  the full candidate fingerprint is
  5b2f099196ba106c8f705ca51397fe8418ec0dffd027f79212411aa09c7418c7.
  Full-page tiled inference on the immutable development manifest scored
  precision 0.943932, recall 0.987026, detection F1 0.964998, and AP50
  0.984581. Against `separation2/current-zero-cost`, mean paired per-page F1
  improved by +0.280126 with 95% bootstrap CI [0.211781, 0.353389];
  protected-slice F1 deltas were MTH1000 +0.184038, MTH1200 +0.611662, and
  TKH +0.142430. The worst-page overlay confirms high recall and shows the
  remaining error is excess detections, especially on MTH1200: 723 false
  positives overall, 439 on MTH1200; 574 had confidence below 0.5 and 567
  overlapped a gold box above 0.1 IoU. This makes confidence/NMS calibration
  the next bounded lever, not more labels. No Gemini labels were used:
  MTHv2 already supplies human character boxes. Future Gemini boxes may be
  training pre-annotations only after exhaustive human review; they can
  never be evaluation gold. Operational note: the first acquisition command
  omitted `--development-only` and was canceled before any qualification
  manifest was built or read; ignored scratch may contain cached test archive
  members, but no test case entered training or evaluation. Qualification
  remains unrun and production remains unchanged.  (development challenger wins)

- 2026-07-24  rf_detr_confidence_040_v1  Expanded the successful RF-DETR
  smoke experiment to the full reserved MTHv2 development set: 240 pages,
  80 per protected corpus, with manifest SHA-256
  fa8521356d1b2bb3b43ff666a9c8ac021e0aeb2ab1eb4e42e13c45fb746d61c2.
  The original threshold 0.25 candidate scored F1 0.964870 and AP50 0.980649.
  Threshold 0.40 was selected on the original 36 pages, then evaluated without
  further tuning on the untouched remaining 204. Held-out F1 improved from
  0.964846 to 0.976407; mean paired per-page F1 improved by +0.010955 with
  95% bootstrap CI [0.007613, 0.014565]. Slice deltas were MTH1000 +0.016077,
  MTH1200 +0.018902, and TKH -0.001236, within the 0.01 protected-slice
  limit. The confidence candidate fingerprint is
  5d3b0a08e0470537af7b719e2113f142681711117708f403bea09632b9de5b8a.
  Across all 240 pages it scored precision 0.979411, recall 0.974068, F1
  0.976732, and AP50 0.971313. The zero-cost baseline scored F1 0.659016 and
  AP50 0.588276; paired mean F1 improved by +0.299992 with 95% CI
  [0.272858, 0.328505], and all protected corpora improved. Residual errors
  concentrate in irregular and faint MTH1000 pages: only 4/240 pages are below
  0.90 F1, while 150/240 are at or above 0.98. Qualification remains unrun
  and production remains unchanged. Next: change only training-data scale and
  composition, using additional human MTHv2 boxes with no pseudo-labels.
  (development challenger wins)

- 2026-07-25  rf_detr_scale300_fixed5_seed361004_v1  Tested the predeclared
  data-plus-exposure treatment: 300 balanced human-labeled MTHv2 pages versus
  a nested 90-page control, both from the same RF-DETR Nano pretrained
  checkpoint, runtime, seed 361004, deterministic settings, five complete
  epochs, and threshold 0.40. The 300-page dataset contained 10,079 tiles and
  151,307 boxes; the 90-page control retained 2,944 tiles and 46,044 boxes.
  The immutable 36-page validation manifest was identical in both arms.
  Training and development manifests had zero case, source-path, image,
  text-line-label, or character-label hash collisions. At the smoke gate, the
  five-epoch challenger scored precision 0.986819, recall 0.898476, F1
  0.940577, and AP50 0.894387 versus control F1 0.978532. Mean paired per-page
  F1 changed by -0.037728 with 95% bootstrap CI [-0.042737, -0.032388], and
  every protected corpus regressed. The predeclared full gate was stopped.
  This rejects five epochs of proportional exposure, not the 300 human-labeled
  pages themselves. Qualification and production remain unchanged.
  (rejected)

- 2026-07-25  rf_detr_scale300_epoch0_threshold031_v1  Audited all five regular
  checkpoints from the rejected scale run and confidence 0.05-0.50 on the
  frozen 36-page selection set. Epoch zero at threshold 0.31 scored precision
  0.980123, recall 0.983620, F1 0.981868, and AP50 0.981058. Against the prior
  threshold-0.40 champion, mean paired per-page F1 improved by +0.003414 with
  95% bootstrap CI [0.000516, 0.006318], and all protected corpora improved.
  The frozen epoch-zero checkpoint and threshold were then evaluated without
  retuning on the untouched 204 pages: precision 0.976897, recall 0.979706, F1
  0.978299, and AP50 0.976607. Mean paired F1 improved by +0.002160 with 95%
  CI [0.000527, 0.003898]; slice deltas were MTH1000 +0.003556, MTH1200
  -0.000311, and TKH +0.001804. Across all 240 pages, F1 was 0.978846 and AP50
  0.977268; paired mean F1 improved by +0.002348 with 95% CI
  [0.000842, 0.003881], and all corpus deltas were nonnegative. Residual errors
  fell from 3,735 to 3,410 (-8.70%); 220/240 pages are at least 0.95 F1,
  152/240 are at least 0.98, and 4/240 remain below 0.90. The checkpoint
  SHA-256 is
  812d5bfc19f74a301a5eb72a147a35105d700f804948240c783159e8c9747085;
  prediction fingerprint
  6a72b1d1562d0e90b3a850f9f800796177c6693ce045baa98167e16f27c05604.
  The worst residuals remain pale, irregular MTH1000 pages, so the next bounded
  lever is conservative brightness/contrast augmentation at one epoch with all
  other data, seed, model, and inference settings frozen. Qualification remains
  unrun and production remains unchanged.  (development challenger wins)

- 2026-07-25  rf_detr_brightness_contrast010_p030_epoch1_v1  Tested one
  deterministic training lever against the 300-page epoch-zero champion:
  `RandomBrightnessContrast` only, with brightness and contrast limits 0.10
  and probability 0.30. Data, pretrained weights, seed 361004, one-epoch
  exposure, optimizer schedule, tiling, threshold 0.31, and NMS 0.40 were
  frozen. The regular checkpoint SHA-256 is
  baf776bc26862189a4ebe4ab9aaba8fa58f247589a6c3100357e6fba7a774bb3.
  On the 36-page smoke set, it scored precision 0.982002, recall 0.982241, F1
  0.982122, and AP50 0.980266. Although summary F1 was +0.000254, mean paired
  per-page F1 changed by -0.000993 with 95% bootstrap CI
  [-0.005255, 0.002945], so the decision was
  `inconclusive_prefer_baseline`. Slice deltas were MTH1000 -0.004067,
  MTH1200 +0.002767, and TKH +0.002655. Eighteen pages improved, fifteen
  regressed, and three were unchanged; the targeted MTH1000 slice worsened.
  The required positive confidence bound was not met. The untouched 204-page
  and full 240-page gates were therefore not run. The epoch-zero threshold-0.31
  candidate remains the development champion. Qualification and production
  remain unchanged. Next permitted action: stop global photometric augmentation
  and obtain source-book-grouped M5HisDoc-R evidence before another
  domain-robustness experiment.  (inconclusive, prefer baseline)

- 2026-07-25  rf_detr_mth600_vs_kuzushiji_transfer_epoch1_v1  Tested two
  one-epoch RF-DETR Nano treatments at exactly 21,275 training tiles: a
  600-page, 308,292-box MTHv2 control and the original 300 MTHv2 pages plus
  11,196 deterministic tiles from 600 CODH Kuzushiji v2 pages across all 44
  books. The Kuzushiji source manifest contains 118,018 human boxes and has
  SHA-256 `0fcc836a60011f60d394bd6503533fc9595087eda3c310a1585c21abf7f26051`.
  Model, pretrained weights, seed 361004, optimizer, one-epoch exposure,
  tiling, threshold 0.31, and NMS 0.40 were frozen. The Chinese control and
  transfer regular checkpoint SHA-256 values are respectively
  `cdc06d36dd2273e139571b3196d58c13dee11211ec847fadffa9fee3af46624d` and
  `962eac4b57b34769090862e3f493f47560d5c9f9945c7213273315bbb5011796`.
  On the 36-page gate, transfer F1 was 0.974129 versus control F1 0.988387.
  Mean paired per-page F1 changed by -0.014823 with 95% bootstrap CI
  [-0.021637, -0.009120], and every protected corpus regressed. The
  cross-script candidate was rejected without touching its 204-page holdout.
  The Chinese control independently beat the 300-page champion at the gate,
  then scored F1 0.986694 and AP50 0.987095 on its untouched 204 pages, with
  paired mean F1 +0.008548 and 95% CI [0.006648, 0.010723]. Across all 240
  pages it scored precision 0.985084, recall 0.988830, F1 0.986953, and AP50
  0.987321. Paired mean F1 improved by +0.008058 with 95% CI
  [0.006422, 0.009965], and protected-slice deltas were MTH1000 +0.010039,
  MTH1200 +0.009187, and TKH +0.004726. The 600-page Chinese candidate becomes
  the new development champion. Qualification and production remain unchanged.
  (Chinese scale challenger wins; Kuzushiji transfer rejected)

- 2026-07-25  align_rfdetr_mth600_development_v2  Integrated the frozen
  600-page RF-DETR checkpoint as the same-socket `align/rfdetr-mth600/v1`
  station variant and compared candidate `align/rfdetr-mth600-development-v2`
  against `align/current-default-v1` on three balanced, official-annotation
  MTHv2 development pages. The first adapter smoke exposed adjacent-column
  merging from wide overlapping detections; v2 removed overlap-based grouping
  and clusters by center distance. Under suite
  `align/mthv2-development-v2`, box precision improved from 0.014260 to
  0.582158, box recall from 0.007937 to 0.558326, and normalized coordinate
  error from 0.218237 to 0.079711. All three primary comparisons, all hard
  limits, and every protected slice passed; fabricated-coordinate rate
  remained zero. TKH precision/recall reached 0.967164/0.964286 and MTH1200
  reached 0.779310/0.710692, but MTH1000 precision and recall remained zero,
  tying the baseline and requiring further geometry work. Challenger mean
  latency was 45.51 seconds per page versus 2.74 seconds for the baseline,
  because the isolated runtime loads the checkpoint for every cell. Report
  fingerprint:
  `3feeb2b86d58612dddd57ccfc4370c288139c7ae960195a614d12a3755a12ca3`.
  The report is rejected only because the suite is deliberately
  non-qualification-eligible. Production recipe unchanged. Next permitted
  action: diagnose MTH1000 column association and replace per-cell model loads
  with a bounded persistent local runtime before constructing qualification
  evidence.  (development challenger passes measured gates; not qualified)

- 2026-07-26  align_rfdetr_mth600_qualification_v1  Corrected the
  `align/rfdetr-mth600/v1` adapter by using frozen upstream transcription
  regions as line-level reading-order boundaries, then replaced per-page model
  loading with a checkpoint-keyed isolated localhost worker that exits after a
  bounded idle period. The three-page development smoke raised MTH1000 box
  precision and recall from zero to 0.993464 while reducing challenger mean
  latency from the v2 run's 45.51 seconds/page to 6.03 seconds/page including
  cold startup. A separately frozen qualification suite selected 12 admissible
  pages per corpus from the untouched official MTHv2 test split; 11 candidate
  pages with malformed or internally inconsistent official annotations were
  excluded before candidate execution and recorded in the dataset identity.
  On all 36 sealed cases, candidate precision was 0.987165 versus 0.029898,
  recall was 0.984064 versus 0.017906, and normalized coordinate error was
  0.000985 versus 0.182464. The 95% paired intervals were
  [0.938963, 0.971968] for precision, [0.953101, 0.976652] for recall, and
  [-0.218685, -0.147703] for coordinate error. All absolute hard limits and all
  TKH, MTH1000, and MTH1200 protected-slice policies passed; candidate success
  was 36/36, mean latency was 2.70 seconds/page, and p95 latency was 6.68
  seconds/page. Report fingerprint:
  `e91c0449488430b2154a5ad440e50c4904e025a5cac4469181eafd3251be0284`.
  The experiment record is
  `experiments/ocr_benchmark/align_rfdetr_mth600_qualification_v1.json`; 366
  artifacts were re-hashed into the local content-addressed store. Decision:
  `qualified`, then `proposed`. Proposal
  `8327ad09d50606c15b416d9c73c1270f498f8eba7f1ee3efa78ff3e6b0a4daa7`
  changes only the `align` slot of `chinese_scroll`; current recipe hash
  `b851d65f9b01458868dc21e575f194834f010b715ab6633402273f99d284b3d1`
  would become
  `c7a53921caf5971f5e6b841bf28568d7fc9ef779b533b5d2bd9c8f936579b1fc`.
  With explicit approval from Dexin Huang <dh3172@columbia.edu>, the protected
  canary ran on all three pages of `gallica_pelliot_chinois_3477`. All 16
  required downstream outcomes passed, and the book, EPUB, and static site
  validated. Known cost was $0.0644175, but agent-backed `reference`, `emend`,
  and `finalize_edition` work remained unpriced, so `unknown_cost` was true and
  the canary status was `unknown`. Promotion correctly refused a non-passing
  canary. The recipe remains byte-for-byte at its pre-proposal hash and no
  promotion history record exists. The complete 98-file isolated canary
  workspace, proposal, and terminal canary evidence were copied into the local
  content-addressed store. Next permitted action: add auditable agent usage and
  cost evidence, then produce a new canary identity; never coerce unknown cost
  to zero.  (qualified and proposed; canary behavior passed; promotion blocked
  by unknown cost)

- 2026-07-28  rf_detr_mth2156_data_scale_epoch1  Trained the one-class
  RF-DETR Nano for one frozen epoch on every admissible content-unique MTHv2
  training page after the 240-page development reserve. Of 2,159 eligible
  official pages, two had non-positive official character boxes and one
  duplicated an earlier image byte-for-byte; all three exclusions are recorded,
  leaving 2,156 pages, 77,415 tiles, and 1,071,028 boxes. The RTX 4090 run used
  seed 361004 and the unchanged 512/96 tiling, optimizer, initialization, and
  0.31/0.40 inference thresholds. Checkpoint SHA-256:
  `f77d91bba64891a79f70fbabaeaca95e104a1d94c2db46fe7d20b41012e95f8c`.
  On the frozen 36-page smoke, paired mean page F1 improved by 0.003258 with
  95% CI [0.001178, 0.005473], and every corpus improved. On all 240 development
  pages, precision/recall/F1/AP50 reached
  0.988074/0.991315/0.989691/0.990430. Paired mean page F1 improved by 0.003142
  with 95% CI [0.002025, 0.004403]; protected-slice F1 deltas were +0.003138
  MTH1000, +0.004768 MTH1200, and +0.000414 TKH. Registered development
  candidate `align/rfdetr-mth2156-development-v1` has fingerprint
  `8a950a70b3f8023459e5d3092cf447cd6715664ddbb1fabadcf2129481ba830e`.
  RunPod cost was $4.212015 and cleanup left zero active Pods and $0/hour spend.
  Official test, qualification, proposal, promotion, and production remained
  untouched.  (frozen development winner; qualification not run)

- 2026-07-28  align_rfdetr_mth2156_qualification_v1  Ran the frozen
  full-data checkpoint through the complete pre-existing authorizing
  `align/mthv2-test-qualification-v1` suite: 36 sealed official-test pages,
  balanced at 12 each from TKH, MTH1000, and MTH1200. Training, development,
  and qualification image hashes were mutually disjoint. All 36 baseline and
  challenger subprocess cases succeeded. Candidate precision/recall reached
  0.988868/0.986097 versus 0.029898/0.017906 for the current deterministic
  baseline; mean normalized coordinate error fell from 0.182464 to 0.000928.
  All six hard limits and every protected-slice comparison passed. Mean and p95
  challenger latency were 3.44 and 8.46 seconds, known cost was zero, and no
  required evidence was unknown. Report fingerprint:
  `3e38c3c0ba55b067eaacda67563e58e767e86b5cab484c9149b90e91c1d20053`.
  A terminal zero-case preflight record was preserved separately after a
  zero-dollar ceiling stopped dispatch; the successful fresh run used a finite
  $1 ceiling and incurred $0. The formal suite is 36 pages, not every page in
  the 800-page official test index; 629 pages satisfy the stricter scorer
  admissibility checks and remain available for broader non-authorizing audit.
  Production, proposal, canary, and promotion remain unchanged.  (qualified)

- 2026-07-28  align_rfdetr_mth2156_official_test_v1  Corrected the
  broader audit corpus to use every image-backed official-test page rather
  than requiring unrelated text-line annotations: 796 of 800 pages were
  scorable, with four malformed official character annotations excluded.
  The frozen 2,156-page RF-DETR checkpoint completed all 796 cases locally.
  Precision/recall/F1/AP50 were
  0.989580/0.991390/0.990484/0.990674, improving over the frozen 600-page
  predecessor by +0.003211/+0.001976/+0.002595/+0.002375. Corpus F1 was
  0.987535 MTH1000, 0.991587 MTH1200, and 0.992578 TKH. The residual audit
  found misses concentrated in small or faint interlinear and marginal
  characters, while small marks and annotation-scope mismatches caused false
  positives. Sparse pages below 100 boxes and very dense pages above 600
  boxes were the weakest density bands. Record:
  `experiments/ocr_benchmark/align_rfdetr_mth2156_official_test_v1.json`.
  Production remained unchanged.  (official-test audit passed)

- 2026-07-28  align_rfdetr_tile384_development_v1  Tested one causal
  response to the small-character residual: reduce inference tiles from 512
  to 384 pixels while holding the checkpoint, 96-pixel overlap, 0.31
  threshold, and 0.40 NMS IoU fixed. On the frozen 36-page development smoke,
  the smaller tile recovered only two true positives but added 535 false
  positives. F1 fell from 0.991543 to 0.970614. A confidence sweep found its
  best F1, 0.989870 at threshold 0.50, still below the 512-pixel baseline.
  The challenger was rejected before the 240-page run. Record:
  `experiments/ocr_benchmark/align_rfdetr_tile384_development_v1.json`.
  (rejected)

- 2026-07-28  read_paddleocrvl16_ancientdoc_preflight_v1  Froze the newest
  open PaddleOCR-VL challenger locally at repository revision
  `66317acc4c9fc17bd154591ce650735cd2855f3e`; its 1.8 GB weight file hashes
  to `85a479d506a11e724e7285d395c551be69f41dbc16b6342d3cacfb189aed71db`.
  Added a pinned AncientDoc acquisition path and materialized 28 development
  pages balanced across 14 semantic categories plus a 341-page,
  book-disjoint qualification reserve. The full OCR benchmark test file
  passes 24 tests. Five RunPod allocations never reached reported container
  uptime or SSH readiness and were deleted before data transfer; local
  Transformers paths failed before generation on incompatible RoPE,
  causal-mask, or missing-key APIs. No prediction was claimed. Cleanup left
  zero active Pods and $0/hour spend; the observed balance delta was
  $0.187735. Record:
  `experiments/ocr_benchmark/read_paddleocrvl16_ancientdoc_preflight_v1.json`.
  Next: run the official full pipeline in a healthy pinned container on the
  28 development pages, then compare character error rate against the current
  production reader.  (preflight complete; screening pending)

- 2026-07-28  read_paddleocrvl16_ancientdoc_development_v1  Retried the
  official PaddleOCR-VL 1.6 pipeline on RunPod. One community RTX 3090 Pod
  again failed readiness, but a secure A40 Pod created with the current
  `runpodctl pod create` interface exposed SSH and completed all 28 frozen
  AncientDoc development pages. The downloaded model weights matched the
  frozen SHA-256 exactly. Coverage was 28/28 with no empty outputs. Mean
  normalized character error rate was 0.738891 and median was 0.314823;
  micro CER was 1.067363 because one vertical astronomical table produced
  6,052 characters of repeated content against an 840-character reference.
  Mean model latency was 12.48 seconds per page. The predictions hash to
  `949cd17e9bf3c2f352d9031a00c4709b8e1a399848f70df5d79d2377d892ce98`.
  Both Pods were deleted; observed retry cost was $0.263496 and cleanup left
  zero active Pods at $0/hour. This is unpaired development evidence, so
  production remains unchanged. Record:
  `experiments/ocr_benchmark/read_paddleocrvl16_ancientdoc_development_v1.json`.
  Next: create a formal non-authorizing AncientDoc read suite and compare the
  frozen challenger with the current production reader on the same 28 cases.
  (development screen complete; paired comparison pending)

- 2026-07-28  read_kimivl_a3b_ancientdoc_development_v1  Tested the
  layout-free `moonshotai/Kimi-VL-A3B-Instruct` challenger at frozen revision
  `398eede0903cd983a2bfa0cc634e9ac1d843f375` on four declared AncientDoc
  development pages. The 16B-parameter BF16 checkpoint ran on a secure A40,
  but two hard pages reached the 8,192-token ceiling: the astronomical table
  repeated `已初一刻` and produced 10,243 characters against 840 gold
  characters, while the Chu Ci page produced 8,498 against 227. Their output
  ratios, 12.19 and 37.44, exceeded the declared 2.0 smoke limit. Kimi's
  four-page macro CER was 12.373512 and micro CER was 10.988208, versus
  PaddleOCR-VL's 2.365300 and 3.731722 on the identical pages. Kimi slightly
  improved one ordinary page, 0.085492 to 0.080311 CER, but the smoke gate
  correctly stopped the 28-page run. The Pod was deleted after preserving the
  four predictions; observed cost was $0.221919 and cleanup left zero active
  Pods at $0/hour. Record:
  `experiments/ocr_benchmark/read_kimivl_a3b_ancientdoc_development_v1.json`.
  Next: test bounded column crops driven by the proven RF-DETR localization,
  not another unconstrained full-page generative VLM.
  (rejected at smoke gate)

- 2026-07-28  read_clean_sheet_ancientdoc_development_v1  Lifted the 1,857
  RF-DETR character boxes from the same four AncientDoc smoke pages onto
  same-size white canvases. All boxes rendered and the pages became
  82.46%-93.60% pure white; visual audit confirmed that parchment, gutters, and
  page boundaries disappeared while character geometry remained legible. The
  causal Kimi-VL rerun rejected the hypothesis: the astronomical table repeated
  to exactly the same 10,243 characters and 12.046429 CER, and the dense Chu Ci
  page still reached the 8,192-token ceiling with 7,192 characters and
  31.506608 CER. Both ordinary pages regressed, from 0.080311 to 0.103627 and
  from 0.226337 to 0.353909 CER. Four-page macro CER fell from 12.373512 to
  11.002643 only because the still-catastrophic Chu Ci repetition was shorter;
  median CER rose from 6.136383 to 6.200169. The maximum output/reference ratio
  was 31.68 against the declared 2.0 gate. The Pod was deleted, leaving zero
  active Pods at $0/hour; the conservative balance-delta upper bound was
  $0.437348. Production remains unchanged. Record:
  `experiments/ocr_benchmark/read_clean_sheet_ancientdoc_development_v1.json`.
  Next: split boxes into bounded columns or short regions and assemble their
  transcriptions deterministically; do not use another same-layout full-page
  Kimi run. (clean rendering validated; OCR hypothesis rejected)

- 2026-07-28  read_gemini_geometry_adjudicator_development_v1  Ran the
  production Gemini reader identity (gemini-3.6-flash, read/zh/diplomatic,
  temperature 0.1, high media resolution, low thinking) as loose full-page
  transcription on all 28 AncientDoc development pages, plus RF-DETR boxes,
  deterministic column clustering, and a geometry-grounded agentic adjudicator
  that saw page, box overlay, column counts, and transcription. Gemini beat
  the PaddleOCR-VL full pipeline on 24 of 28 pages: macro CER 0.443086 vs
  0.738891, micro 0.492846 vs 1.067363, with zero repetition events and a
  maximum output of 852 of 32,768 tokens; the astronomical table read at
  0.852381 CER where Paddle produced 6.794048 and Kimi 12.046429. The
  deterministic count gate flagged exactly one page (ratio 15.54), a true
  detector failure: 50 boxes on a ~777-character spread whose left folio
  RF-DETR missed entirely, while the transcription there stayed faithful. The
  adjudicator caught all three catastrophic pages (CER >= 1.0, all routed
  column_mode), diagnosed per-page which component failed, and named concrete
  misreadings (逐 for 遂, 荀 for 苟), but over-routed four healthy pages
  (CER <= 0.3) to column_mode, failing the declared <= 2 precision gate.
  Total Gemini cost was $0.852326 of the $5 budget; no paid GPU. Production
  remains unchanged. Records:
  `experiments/ocr_benchmark/read_gemini_geometry_adjudicator_preflight_v1.json`,
  `experiments/ocr_benchmark/read_gemini_geometry_adjudicator_development_v1.json`.
  Next: bounded column-mode reading for flagged pages with count-anchored
  token ceilings and right-to-left assembly, paired against these full-page
  transcriptions. (reader baseline adopted; adjudicator recall validated,
  routing precision insufficient)

- 2026-07-28  read_column_agreement_development_v1  Transcribed all 565
  detector-derived columns on the 28 AncientDoc development pages with the
  frozen Gemini identity and count-anchored token ceilings, then measured the
  dual-channel agreement ceiling against the existing full-page channel.
  Ceiling engineering took three smokes: a 128-token floor starved tiny
  columns because thinking shares the output budget; at 512, 13 of 22 smoke
  columns died mid-thought; at 1024 with low thinking, 6 of 22 still spiraled
  stochastically (different columns each run, every retry succeeded). Minimal
  thinking eliminated truncation entirely: 565/565 columns, zero failures,
  27-34 output tokens each, $1.394119 total. Column-mode assembly is worse
  than full-page everywhere (macro 0.540955 vs 0.443086; all three
  catastrophic pages regressed), because isolated crops lose page context and
  faithfully read commentary the gold ignores; the adjudicator-routed system
  (0.502729) also loses to plain full-page, so the champion stands. The
  consensus measurement validated the architecture direction: characters
  where both channels agree cover 89.06% of channel A and score 0.947535
  micro precision on the 12 healthy pages (macro 0.964256, 1.000000 on the
  cleanest page, recall 0.91-0.97), versus 0.62-0.75 for any single channel,
  but the 0.99 gate failed: residual error concentrates in gold-scope
  mismatch (unscored interlinear commentary) and diagram-scrambled column
  order, not silent misreading. Gates: completion and truncation passed;
  catastrophic improvement, routed non-regression, and the 0.99 consensus
  target failed. Production remains unchanged. Records:
  `experiments/ocr_benchmark/read_column_agreement_preflight_v1.json`,
  `experiments/ocr_benchmark/read_column_agreement_development_v1.json`.
  Next: classify boxes into primary versus half-width commentary layers,
  score the champion against scope-filtered evidence, and adjudicate only
  disagreement spans with their crops. (ceilings validated; column
  replacement rejected; consensus 0.95-1.00 on healthy pages)

- 2026-07-28  read_layered_consensus_development_v1  Built the deterministic
  scope layer (optimal two-split of per-page box widths) and it separated the
  corpus exactly along the failure map: all three catastrophic pages and all
  four mid-CER commentary pages are two-layer, every healthy page one-layer.
  A layered read (primary and commentary fields, count anchors, frozen
  gemini-3.6-flash identity, $0.168434) became the best single-pass result:
  macro CER 0.385179 versus the 0.443086 full-page champion, with the Chu Ci
  page collapsing 2.136564 to 0.475771 and zero adjudication involved. Two
  gold-scope regressions (0.315 to 0.671 and 0.319 to 0.506) exposed a
  measured property of AncientDoc: gold includes small-character text in some
  volumes and excludes it in others, so no single scope matches all books;
  oracle per-book scope selection reaches 0.355552. Two one-layer pages came
  back with empty primary fields (the uniformly-small astronomical table and
  the detector-failure spread), repaired deterministically and recorded as a
  failed completion gate. The dispute stage adjudicated 178 of 293 disagreeing
  columns before the $3 sub-budget stop fired ($3.079709 observed): isolated
  crop re-reads regressed most healthy pages (0.078 to 0.156, 0.036 to 0.050)
  while zero-adjudication pages kept layered lines and improved, confirming
  page context is load-bearing, the same lesson as column-mode. Column-exact
  consensus precision on healthy pages rose to 0.968266 micro and 0.974178
  macro with four pages at 1.000000, but coverage fell to 37.7 percent of
  gold, so production consensus must be character-grain within column
  alignment. Final pipeline macro 0.409656 beat the champion but only through
  the scope wins. Total cost $3.248143 of $4; production unchanged. Records:
  `experiments/ocr_benchmark/read_layered_consensus_preflight_v1.json`,
  `experiments/ocr_benchmark/read_layered_consensus_development_v1.json`.
  Next: deterministic per-book scope selection, character-grain consensus
  inside column alignment, and a disagreement pass that shows the full page
  with the disputed column highlighted. (layered read adopted as best
  single pass; crop-isolated adjudication rejected; gold scope inconsistency
  measured)

- 2026-07-28  read_scope_selection_consensus_grain_v1  Zero-cost deterministic
  rescore of frozen artifacts, no model calls. Per-book scope selection
  between the layered primary channel and the full-page champion, derived
  from the two development pages of each of the 14 volumes, scored 0.358035
  macro CER (median 0.305140) against 0.443086 full-page, 0.385179 layered,
  and the 0.355552 per-page oracle; selection regret is 0.002483, and the
  corpus partitions into nine primary-scope and five full-scope books,
  converting the measured gold-scope inconsistency into recorded evaluation
  metadata. Character-grain consensus inside the frozen column pairing
  recovered coverage: 91.07% recall of healthy-page gold at 0.946730 micro
  and 0.964114 macro precision, versus the column-exact point's 37.75%
  recall at 0.968266, giving the verification architecture a measured
  two-point precision-coverage frontier; the healthy-page floor is 0.853916
  on one dense worn-print spread where both channels repeat identical
  misreadings, invisible to any two-channel agreement rule. Gates: scope
  macro passed; consensus recovery partial (recall and macro precision
  passed, micro precision 0.946730 short of 0.96); immutability verified.
  $0 spent; production unchanged. Records:
  `experiments/ocr_benchmark/read_scope_selection_consensus_grain_preflight_v1.json`,
  `experiments/ocr_benchmark/read_scope_selection_consensus_grain_development_v1.json`.
  Next: the paid full-page-context disagreement pass over non-consensus
  residue, and scope-flag or gold-version handling for reserve books before
  any qualification use. (scope flags adopted as evaluation policy;
  character-grain consensus adopted as verification signal)

- 2026-07-28  transcribe_toolbelt_rig_development_v1  Stood up the Exodia
  tool-bearing Rig: a new transcribe/omp_toolbelt station variant whose OMP
  agent (openai-codex/gpt-5.6-luna) drives host-authored tools inside the
  evaluated cell. The station one-shots the production-pinned RF-DETR
  runtime (checkpoint cdc06d36, tile 512, overlap 96, threshold 0.31,
  nms 0.4), stages evidence/geometry.json with a right-to-left column map,
  a detection overlay, and padded per-column crops, and grants exactly
  read (images/ and evidence/), deterministic verify_counts, and single-use
  sealed submit_transcription. Both seed extensions are identical no-ops so
  the paired delta measures the host harness alone. Run dev-1 failed on the
  challenger side because the isolated case root hid the checkpoint;
  PALIMPSEST_RFDETR_OBJECT_ROOT is the designed override and dev-2 completed.
  On the visible Borg.cin.361 f004r case the agent made 43 turns and 41 tool
  calls versus the baseline's 7 and 5, and the finished sealed transcription
  improved paired character error 0.766571 to 0.533141 while halving
  invented characters 0.271100 to 0.121294, converting the one hard limit
  the evidence-free baseline fails into a pass; completeness stayed 1.0 and
  contamination, empty-output, and repetition stayed zero. Total spend
  $0.747695 of $6; 105 targeted factory tests passed; production recipes
  unchanged. Records:
  `experiments/ocr_benchmark/transcribe_toolbelt_rig_preflight_v1.json`,
  `experiments/ocr_benchmark/transcribe_toolbelt_rig_development_v1.json`;
  report `library/evaluations/runs/exodia-toolbelt-rig-dev-2/report.json`.
  Next: adopt the toolbelt lane in the exodia campaign as a new campaign
  identity with PALIMPSEST_RFDETR_OBJECT_ROOT set, and let the proposer
  search pure-policy candidates on the tool-bearing harness. (rig validated;
  agent-driven tools produce the finished evaluated output)

- 2026-07-28  transcribe_toolbelt_ancientdoc_development_v1  Promoted AncientDoc
  to a first-class factory evaluation: transcribe/ancientdoc-development/v1,
  28 cases on dataset gold with per-volume scope flags riding as protected
  slices, images seeded content-addressed, gold marked
  development_non_qualifying, all built deterministically by
  make_ancientdoc_transcribe_suite.py and bench-verified. Ran the tool-bearing
  omp_toolbelt Rig paired against the image-only baseline (both
  openai-codex/gpt-5.6-luna) on the four declared smoke cases. Smoke v1
  stopped after one case when the baseline flaked with a duplicate
  submission and the unknown-cost policy halted dispatch as designed; v2
  completed 4 of 4 pairs at $1.8938 with zero repetition anywhere and all
  aggregate hard limits passing. The rig won its design case decisively:
  table CER 1.3488 to 0.8726 with invented characters collapsing from
  0.3796 to exactly 0.0000. It lost everywhere gold excludes commentary
  (Chu Ci 0.6035 to 2.0000, invented 0.5954; ordinary pages roughly doubled),
  because box-complete transcription is the wrong target on scope_primary
  volumes: the measured gold-scope inconsistency resurfaced inside the rig.
  The permitted full 28-case run was withheld on this evidence, saving the
  budget for toolbelt v2: layered submission with scope-aware verify_counts
  against primary-layer boxes and full-page-first reading. Observed spend
  $2.3936 of $20; production unchanged. Records:
  `experiments/ocr_benchmark/transcribe_toolbelt_ancientdoc_preflight_v1.json`,
  `experiments/ocr_benchmark/transcribe_toolbelt_ancientdoc_development_v1.json`.
  (suite adopted as a durable asset; anti-hallucination property confirmed;
  toolbelt v1 rejected as universal reader; v2 direction fixed)

- 2026-07-29  transcribe_toolbelt2_ancientdoc_development_v1  Built and
  adopted toolbelt v2, the harness repair driven directly by the v1 smoke
  evidence: layered primary-plus-commentary submission verified against the
  deterministic width-split, page-first reading with column crops demoted to
  verification, and a staged independent second reader per column
  (gemini-3.6-flash, minimal thinking, count-anchored ceilings) surfaced
  through the new verify_layers tool. The four-case smoke swept 4 of 4 pairs
  (paired mean 0.359925 versus baseline 0.454350; the v1 Chu Ci catastrophe
  collapsed from 2.0 to 0.431718 with zero invented characters), authorizing
  the full 28, which ran as four concurrent chunk runs and completed 28 of
  28 pairs with zero failures and zero repetition: macro CER 0.545034 to
  0.371389, median 0.578580 to 0.273847, 25 of 28 pairs won, invented
  characters halved, both protected scope slices improved, and all hard
  limits passed in every chunk. The rig now matches the day's best
  single-pass system (0.358035 per-book-scope Gemini) on a different model
  without scope flags, while emitting layered auditable output. Companion
  deliverables shipped in the same push: the Kuzushiji development suite (24
  cursive pages, derived reading-order gold, bench-verified, the standing
  hard external gate) and the first gold-factory pilot (two independent
  drafts of Borg.cin.361 f005r through the exodia draft queue with the
  r_train partition gate enforced, correction template awaiting the
  operator). Spend $11.1031 of $17; production unchanged. Records:
  `experiments/ocr_benchmark/transcribe_toolbelt2_ancientdoc_preflight_v1.json`,
  `experiments/ocr_benchmark/transcribe_toolbelt2_ancientdoc_development_v1.json`.
  Next: point the exodia campaign challenger lane at omp_toolbelt2, run the
  Kuzushiji gate, and continue the gold factory. (v2 adopted; every v1
  failure repaired; rig at parity with the day's champion)

- 2026-07-29  transcribe_toolbelt2_mthv2_development_v1  Answered "where do we
  have actual gold" by promoting MTHv2's manually annotated text lines to a
  transcribe suite: transcribe/mthv2-development/v1, 24 official-test pages,
  eight per corpus, gold method official_annotation with the print-perfect
  0.15 invented allowance, images staged content-verified, bench-verified.
  The adopted omp_toolbelt2 rig then beat the image-only baseline on real
  human gold: smoke 4 of 4 (0.186350 versus 0.248500), full 24 as four
  concurrent chunks completing 23 pairs (one baseline duplicate-submission
  flake, the second in about sixty sessions; the toolbelt has never flaked):
  macro CER 0.268503 to 0.209306, median 0.271468 to 0.187500, 16 of 23
  pairs, invented characters 0.041100 to 0.009600, every protected corpus
  slice improved, all hard limits passed. Cross-suite reading: the same
  immutable rig scores 0.371389 on AncientDoc and 0.209306 on MTHv2, so a
  0.16 macro gap is evidence-side, quantifying why real gold is the only
  trustworthy compass. New target class measured: two dense MTH1200 Gaoli
  commentary pages showed case-level repetition (0.1739, 0.2273) inside a
  passing aggregate, so v3 must bind repetition at case grain on
  two-register layouts. Spend $9.8906 of $17; production unchanged. Records:
  `experiments/ocr_benchmark/transcribe_toolbelt2_mthv2_preflight_v1.json`,
  `experiments/ocr_benchmark/transcribe_toolbelt2_mthv2_development_v1.json`.
  Next: harness v3 (per-register anchors, repetition check in verify_layers,
  sealed baseline submissions), diagnose the MTH1200 residue, then hand the
  rig to the exodia campaign proposer against the three standing gates.
  (real-gold gate adopted; rig advantage confirmed on official annotations)

- 2026-07-29  transcribe_toolbelt3_development_v1  Built harness v3 (register
  -aware count anchors, a suite-exact repetition mirror per layer inside
  verify_layers, a detector-trust verdict that suppresses misleading anchors,
  and first-wins sealed-submission tolerance for the baseline and layered
  readers after two observed duplicate-attempt flakes; 34 tests pass) and ran
  the targeted four-case smoke against the adopted v2 on the real-gold gate.
  The smoke falsified the gate premise instead of the harness: the two Gaoli
  "repetition" pages are canon catalog pages whose GOLD legitimately repeats
  右N經同本異譯 four to six times (gold repetition_rate 0.095238 and
  0.157895), so a perfect transcription of GL-1054-1-13 would fail the
  suite's absolute 0.1 hard limit and the declared zero-repetition target
  was unsatisfiable; the stable measured rates were page structure, not
  loops. v3 performed well regardless: 4 of 4 sealed, smoke mean 0.316125
  versus v2's 0.529125 in-run, invented characters at most 0.0364, healthy
  control 0.1172 while the v2 side flaked to 0.8682 on a page it previously
  read at 0.0816, exposing a v2 layer-misroute variance mode. Full run
  withheld pending a corrected gold-relative repetition gate and a suite
  revision that derives per-case repetition allowances from gold (metric
  changes require a new suite fingerprint). Spend $2.0316 of $20; production
  unchanged. Records:
  `experiments/ocr_benchmark/transcribe_toolbelt3_preflight_v1.json`,
  `experiments/ocr_benchmark/transcribe_toolbelt3_development_v1.json`.
  Next: v3.1 preflight with the gold-relative repetition gate, suite
  revision per governance, a layer-misroute self-check, then the full 24
  and the AncientDoc cross-suite smoke. (v3 delivered; absolute repetition
  hard limit falsified on real gold; catalog-page false-alarm class named)

- 2026-07-29  transcribe_gold_judge_development_v1  Adopted the operator's
  evaluation lens and retired mechanical gates: gold-anchored grading where
  transcribing more than the reference is never an error. Two passes over
  the 111 immutable stored outputs, no new transcriptions: deterministic
  insertion-free gold recall (matched gold characters over gold length),
  and an answer-key judge (gemini-3.6-flash, schema-bound, never shown the
  image or side identity). Under this lens the rig's advantage widens
  everywhere: AncientDoc judged answer recovery 0.6076 to 0.8353 mean
  (26 of 28 pairs), MTHv2 real gold 0.8287 to 0.9591 mean with median
  0.9812, four perfect and fifteen near_perfect of 24 pages and zero poor
  (21 of 23 pairs); deterministic gold recall agrees on every ranking. The
  judge flagged ZERO of 111 outputs for suspicious extra text, confirming
  the extra-is-free premise, and judged the catalog page condemned by the
  absolute repetition gate perfect at 1.0, closing that question: the
  answer key, not a threshold, is the arbiter, and the v3.1 repetition-gate
  and suite-revision plans are dropped as superseded. Judge spend $0.5533
  of $3; production unchanged. Records:
  `experiments/ocr_benchmark/transcribe_gold_judge_preflight_v1.json`,
  `experiments/ocr_benchmark/transcribe_gold_judge_development_v1.json`.
  Next: gold-anchored recall plus the answer-key judge is the standing
  evaluation; improvements target the judged residue named per page in
  missing_from_output, starting with the AncientDoc poor page and the
  MTH1200 partial class. (operator lens adopted; rig at 0.96 judged answer
  recovery on real gold; zero suspicious extra text)

- 2026-07-29  transcribe_toolbelt4_development_v1  Chased perfection by
  autopsy: aligned every non-perfect real-gold page character-by-character
  and found the residue is 12.1% wrong versus 3.6% missing, with the wrong
  class dominated by variant-normalization pairs our own prompt instructed
  (縁→緣, 爲→為, 寳→寶...). Built v4 as a pure prompt identity on the v3
  harness (exact-form rule with the measured pairs as examples, plus a
  completeness sweep); no new station code. Smoke on the four worst Gaoli
  pages passed (3 strict wins, 1 tie) and exposed a third residue class:
  catalog pages carry bag-of-characters recall 0.87-0.89 against sequence
  recall 0.25-0.48 - panel ordering, not misreading, and the judge already
  scores those pages 0.98-1.0. Full 24: variant-pair errors cut 44%
  (199→112, total wrong 12.12%→10.32%) but reverse-overcorrections appeared
  (為→爲, 說→説) - the model guesses forms it cannot visually resolve, so
  codepoint fidelity is partially irreducible from page-resolution reading.
  Judged answer recovery 0.9621 vs 0.9559 (noise), sequence recall flat
  (0.8426 vs 0.8466) with one new layer-misroute flake, so: inconclusive,
  v2 stays champion, no proposal. Perfection now decomposes into four named
  residues with different instruments: variant ambiguity (glyph-crop
  adjudication against the char_inventory exemplar library), catalog
  ordering (order-aware deterministic lens; judge already correct),
  reliability flakes (harness repeat policy), small interlinear misses.
  Spend $14.44 of $18. Records:
  `experiments/ocr_benchmark/transcribe_toolbelt4_preflight_v1.json`,
  `experiments/ocr_benchmark/transcribe_toolbelt4_development_v1.json`.
  (v4 inconclusive; residue named per class; glyph adjudication is the
  next instrument)

- 2026-07-29  transcribe_glyph_adjudication_development_v1  Asked whether the
  wrong-character residue is decidable at glyph-crop resolution - the
  question v4 left. Re-posed all 931 champion disagreements on MTHv2 real
  gold (exactly the autopsy's 936 wrong chars minus 5 non-CJK) plus 200
  confusable-agreement controls as blind forced choices on the character's
  own gold-annotated crop, enlarged; A/B assignment seeded and balanced
  (571/1131) so nothing leaks which side is gold. The char_inventory
  exemplar library was evaluated and rejected as the instrument: its index
  is unlabeled geometry, so no labeled exemplar exists for any variant
  pair. Verdict: the information IS on the page - fix rate 0.8135 among
  resolved, control break rate 0.0355, patched sequence recall 0.8468 ->
  0.9402 with all 24 pages improving (ordering pages GL-1054 partly
  re-assert per-location identity: excluding both, 0.8968 -> 0.9474). The
  169 kept-output cases name a gold-noise class - blind crop evidence the
  annotators sometimes normalized (巳/已, 圎/圓, 𫝹/念 in reverse) - eligible
  for the gold-correction protocol, never silently. All three declared
  gates passed; $2.51 of $4; production untouched. Records:
  `experiments/ocr_benchmark/transcribe_glyph_adjudication_{preflight,development}_v1.json`,
  fingerprinted artifacts under
  `scratch/ocr_benchmark/runs/glyph-adjudication-v1/`. Next: toolbelt v5
  wires the instrument production-legally - detector-box localization and
  a gold-free trigger set (second-reader disagreements + variant
  watchlist) - then the paired run under both lenses. (instrument
  validated; the largest residue class is decidable at crop resolution)

- 2026-07-30  transcribe_toolbelt5_development_v1  Wired the validated glyph
  instrument into the harness production-legally: omp_toolbelt5 keeps the
  v3 agent surface byte-identical and adds a deterministic post-submission
  pass - align the sealed primary against per-column second readings
  (stdlib difflib), trigger on CJK disagreements plus a frozen 128-char
  mutual-pair watchlist at agreements, localize by RF-DETR boxes (exact
  when reader length matches box count, else proportional), blind seeded
  A/B forced choice per crop, patch primary only, full telemetry and cost
  in the station result. Smoke passed (4/4 sealed, 3/4 strict wins,
  GL-1048-1-25 hit 0.9304 - its best ever). Full 24: judged answer
  recovery 0.9740 vs 0.9605 with 4 perfect / 1 partial vs 2 / 4 and zero
  suspicious; sequence recall 0.8594 vs 0.8448, wins 19/24, pages >=0.95
  jump 2 -> 10; on the 21 non-ordering pages 0.8925 -> 0.9221; adjudication
  722 calls, 347 patches, $1.66, max page patch fraction 12%, zero skips.
  The declared 0.88 mean gate FAILED (0.8594) and is recorded failed: the
  shortfall is exactly the ordering class - GL-1054 pages plus a session
  lottery instance (mth1000-006: bag 0.9706, sequence 0.6059, judged
  0.997) - which single-character adjudication cannot touch. Decision:
  v5 adopted as development champion on preponderance; production
  untouched; $16.00 of $23. Records:
  `experiments/ocr_benchmark/transcribe_toolbelt5_{preflight,development}_v1.json`.
  Next: the binding residue is ORDERING - design the reading-order
  reconciliation experiment; then missing small text. (v5 adopted; wrong
  characters no longer the dominant residue)

- 2026-07-30  v5_audit  Fanned four read-only audit agents over the v5
  evidence (traces, ordering, model identities, capabilities) after the
  operator flagged a possible mid-flight model swap. Confirmed from run
  artifacts: the working tree moved second reader, adjudicator, judge, and
  the instrument scripts to gemini-3.5-flash while v5 executed - the smoke
  ran the 3.6 adjudicator but the full 24-page run earned the adopted
  numbers (judged 0.9740, sequence 0.8594) entirely on gemini-3.5-flash,
  matching the 2026-07-29 production read rollback ("3.5-flash reads
  better"); the fingerprint rotation 29f8831a -> f07b5898 is thereby
  explained, and the v5 result record carries the amendment. Pricing: 3.5
  via genai-prices catalog, 3.6 via fallback - no cost blackhole. .env
  still exports PALIMPSEST_MODEL_READING=google/gemini-3.6-flash against
  the pinned 3.5 production candidate (flagged, not changed). Ordering
  audit: on GL-1054 pages the second-reader channel re-emitted in geometry
  order scores ~0.85 against gold while the agent's own order scores
  ~0.40 with ~0.99 content agreement between them - the geometry already
  knows the right order and a deterministic re-emit pass is the v6 design;
  mth1000-006 is the residual case where neither order matches annotation
  (bag 0.9706). Traces: agent never exploits verify_layers column flags
  for ordering and reads the full image repeatedly. (audit only; no runs,
  no production change)

- 2026-07-30  transcribe_toolbelt6_development_v1  Built the reading-order
  reconciliation pass the v5 audit designed - and the offline replay first
  falsified the design's premise, twice, cheaply: the ordering class is
  LAYER INTERLEAVING (gold reads small-char notes inline; the layered
  draft emits primary then commentary), and contiguous-span claiming
  cannot reassemble interleave, so the pass became combined-draft
  multi-block claims re-emitted in geometry order with leftovers riding
  their preceding block. Replay on stored v5 outputs: catalog trio 0.499
  -> 0.796, 0.509 -> 0.727, 0.793 -> 0.886, all guards holding. Live
  smoke passed (GL-1054 0.2705 -> 0.7872, 0.3848 -> 0.7000; one declared
  expectation falsified and amended before the full run: mth1000-006
  adopts when its session draft is scrambled, recall-neutral). Full 24:
  the trio lifted decisively (0.2766 -> 0.8267, 0.4939 -> 0.7303, 0.7409
  -> 0.8601) but v6.0's trigger also adopted on two SINGLE-LAYER pages
  whose annotation order geometry cannot explain, scrambling good drafts
  (0.9627 -> 0.3234, 0.95 -> 0.5324, content intact at bag 0.96-0.97) and
  cancelling the mean (0.8546 vs 0.8544); judged lens dipped 0.9771 ->
  0.9664, concentrated on reordered pages - the two lenses now disagree
  about reordering. Declared full gates FAILED and are recorded failed;
  v5 stays champion. The evidence names the gold-free fix exactly: all
  five adoptions split perfectly on the page-level two_layer flag, and
  the damaging adoption ended at sequence 0.700 vs reader while healthy
  ones ended >= 0.81 - so v6.1 gates adoption on two_layer=true with a
  0.75 adoption floor, applied to the module post-run (sha 7809f19a) and
  replay-validated: damaged pages no longer adopt, the big lifts survive.
  Spend $17.94 of $24. Records:
  `experiments/ocr_benchmark/transcribe_toolbelt6_{preflight,development}_v1.json`.
  (v6.0 rejected; interleave capability proven; v6.1 ready to run)

- 2026-07-30  vesuvius_research  Fanned four web scouts over the Herculaneum
  effort (Vesuvius Challenge) and verified the load-bearing claims against
  scrollprize.org directly: 45 scrolls scanned, PHerc. 1667 read end to end
  June 25 2026 (first ever, ~22 columns), $1.8M awarded, their two open
  problems are EXACTLY our two residue classes - unwrapping/topology
  (their reading-order problem, $1M prize) and ink-signal generalization
  (their glyph-signal problem, $500k). Their engine is iterative
  pseudo-labeling with human verification (weak labels -> model -> expert
  review -> retrain, ~15 rounds to the 2023 Grand Prize), guarded by an
  anti-hallucination architecture rule (sub-letter receptive fields so
  models cannot draw plausible letters) and a two-stage acceptance gate
  (technical reproducibility + independent papyrologist committee with
  quantified thresholds: 4 passages x 140 chars, <=15% error). Progress
  is measured as corpus coverage (1 of 45 scrolls; 5% of Scroll 1).
  Resolution escalation (2.4um rescans) made ink directly visible where
  ML struggled. Adopted transfers, in order: (1) the pseudo-label loop as
  the blueprint for labeling our 1,959 glyph crops with the validated
  blind adjudicator and training a local glyph classifier - the fourth
  independent evidence channel and the seed of the expert-parity gold
  factory; (2) IIIF native-resolution crop fetch for adjudication
  triggers that resolve illegible (66/722 unresolved in v5); (3) a
  book-level coverage ledger (pages at judged near-perfect-or-better per
  corpus); (4) codify the crop-local-evidence rule: no model may patch
  text without crop-level evidence. Non-transfers: 3D CT stack, virtual
  unwrapping geometry, synchrotron scanning - though their winding-order
  topology priors validate the v6 geometry-order approach and its limits.
  (research only; no runs; next experiments named)

- 2026-07-30  exemplar_labeling_development_v1  Executed the Vesuvius
  bootstrap on our own assets: seed-labeled all 1,959 geometry-only glyph
  crops of the char_inventory bank with one open-vocabulary reading each
  (gemini-3.5-flash, temperature 0, no-normalization rule). 1,928 labeled
  (98.4%), 685 distinct characters, certainty 1,792 high / 132 medium /
  35 low; the bank reads as a Dunhuang pulse-diagnosis manuscript (之 103,
  以/也/者/脉 next; 脉 28 and 脈 17 kept as distinct codepoints - the
  no-normalization rule visibly working). Operator visual spot-check: 琴
  exact, 慮/無 plausible cursive, 隨 vs 隋 the expected hardening class.
  All gates passed; $4.57 of $6.50. SEED labels, development-only. The
  bank now joins the 1,131 blind adjudication verdicts and MTHv2's labeled
  boxes as the local-glyph-classifier training corpus. Records:
  `experiments/ocr_benchmark/exemplar_labeling_{preflight,development}_v1.json`,
  labels sha 8f66795e. Next: hardening pass and classifier-training
  preflight. (asset created; the exemplar bank is labeled)

- 2026-07-30  transcribe_toolbelt6_1_development_v1  Re-ran the reorder
  pass with the two guards the v6.0 evidence named (two_layer adoption
  gate, 0.75 floor) against the v5 champion. Smoke 6/6 with zero
  single-layer adoptions and the trio adopted above floor (006's session
  lottery struck again with the pass provably inactive - attribution
  amendment recorded before the full run). Full 24 (c1 went terminal on
  one 900s agent timeout + budget stop; recovered per doctrine with
  supplemental run c1b): sequence mean 0.9075 vs 0.8595 - the campaign's
  first 0.90+ - trio swept (0.2979 -> 0.8237, 0.3758 -> 0.7636, 0.7772 ->
  0.8679), exactly 3 adoptions (all two-layer, finals 0.82-0.86), zero
  single-layer adoptions, non-adopted pages better than baseline (0.9202
  vs 0.9132). Judged lens: -0.3pt overall (0.9722 vs 0.9751), the whole
  delta the three reordered pages (-3.1 to -4.0pt, still 0.90-0.94) while
  non-reordered pages improved on BOTH lenses; judged gate recorded
  FAIL-marginal per its wording. Decision: v6.1 ADOPTED as development
  champion - reading order is what the edition needs and the trade is
  localized; fragment-placement smoothing named to recover the judged
  dip; the 006 lottery now has three measurements and repeat-and-reconcile
  as its declared instrument. Spend $20.01 of $25. Records:
  `experiments/ocr_benchmark/transcribe_toolbelt6_1_{preflight,development}_v1.json`.
  (v6.1 adopted; ordering residue closed for the interleave class)

- 2026-07-31  transcribe_toolbelt7_priority_development_v3  Added an
  airlocked, agent-directed glyph-inspection tool to frozen v6.1: the
  agent supplies 2-4 literal alternatives for at most eight selected
  cells, receives only crop-local second-reader/classifier evidence, and
  never receives the bulk classifier manifest or unsolicited labels.
  Smoke passed 6/6 (recall 0.9283 vs 0.9150, +0.0132; 13 inspections;
  $3.80). Full selected 24-pair evidence passed every quality and autonomy
  gate: deterministic recall 0.9218 vs 0.9164 (+0.0054), blinded judged
  match 0.9728 vs 0.9683 (+0.0045), 80 inspections on 19 pages (max 8),
  exact five-page two-layer set, zero single-layer triggers/adoptions,
  and all transition invariants. One frozen-baseline 900-second timeout
  was recovered under a new run but preserved as a catastrophic failure;
  the zero-failure gate therefore rejects v7 despite the quality win.
  Known cumulative campaign spend is $24.3138 of $26, but the timed-out
  in-flight request has unknown cost; exact budget compliance is
  unresolved and further paid dispatch is forbidden. Suite, cases, gold,
  prompts, production recipe, and current production model stayed
  unchanged. Records:
  `experiments/ocr_benchmark/transcribe_toolbelt7_{preflight,smoke,development}_v3.json`
  plus `transcribe_toolbelt7_development_recovery_v3.json`. (rejected;
  quality hypothesis supported, reliability gate failed)

- 2026-07-30  han_variant_and_toolbelt8_development  Implemented three
  same-socket development challengers without changing production: Luna-first
  semantic-region transcription with a deterministic Python layout phase,
  a bounded crop-local inspection variant exposing classifier top-five plus
  immediate neighbors, and translation with an authoritative diplomatic source
  plus a position-preserving Han-variant semantic view. Added symmetric
  versioned scoring (`han-variant-table-v1`, 1,701 mappings), an MTHv2 v2
  development suite, and a transparent synthetic translation conformance
  suite. Offline gates passed: 35 focused tests, three suite verifications,
  embedded TypeScript compilation, and isolated wheel import/resource smoke.
  No model call ran because no finite paid-call authorization was supplied;
  all three paired evaluations remain blocked and production is unchanged.

- 2026-07-30  agent_rig_portability  Added deterministic `.palrig`
  export and safe content-addressed import for fixed-model agent candidates.
  Each bundle freezes the canonical candidate, exact skill prompt, station
  source closure, direct runtime package versions, Python version, and OMP
  version when applicable. Import authenticates an operator-supplied archive
  SHA-256, rejects unsafe members, verifies every internal hash, and compares
  the installed prompt, source, and runtime before it writes. Import does not
  execute or install implementation snapshots. Imported candidates are
  untracked and cannot qualify for promotion. No model call or production
  recipe change occurred. (agent rigs are portable across exact compatible
  runtimes)
