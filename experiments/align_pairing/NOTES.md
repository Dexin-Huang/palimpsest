# align_pairing

**Hypothesis.** The exemplar mislabeling (m2, purity 0.2%) is caused by
order-zipped column pairing: one missed image column shifts every later
label. Pair columns to lines by DP on cell-count vs char-count instead;
judge with the same purity audit.

**Result so far.** Visibly better (the 至 row of the contact sheet is now
almost pure; 之 much improved) but audit purity only 0.2% → 1.0%
(soft metric: 1.9%), and intra-class similarity still sits *below*
inter-class. Diagnostic conclusion: at this label-noise level the audit
cannot bootstrap — class means are noise-corrupted, and the ~111
single-instance characters act as crisp distractors. The number
understates true binding accuracy (visual estimate: 40–60% overall,
uneven by column).

**Open next steps.**
1. Hand-labeled ground truth: ~100 crops marked right/wrong to measure
   binding accuracy directly, decoupled from the audit's bootstrap.
2. Then within-column binding (junk blobs from stains match confidently;
   add ink-density/aspect gates to the DP match cost).
3. Metric work last, once labels are measurable.

**Run.**
```
.venv/Scripts/python.exe experiments/align_pairing/candidate.py
```
Side-by-side report: `out/report.html` after running both experiments.
