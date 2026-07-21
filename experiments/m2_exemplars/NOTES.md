# m2_exemplars

**Hypothesis.** The align artifacts on P.3477 are good enough to harvest a
per-scribe exemplar library: every confident aligned character becomes a
normalized crop, instances of the same character agree with each other far
more than with other characters, and misalignments surface as intra-class
outliers rather than passing silently.

**Contract (the socket this would fill).**
`build_exemplars(alignments, images) → index + crops` — the `glyph_exemplars`
artifact of the glyph system; consumed later by the hand font and the
glyph-generation slot.

**Gate.** Leave-one-out nearest-neighbor purity: each instance, classified
against every character's mean ink mask, must land on its own character.
Report purity %, intra-class vs inter-class mean similarity, and the outlier
list. Visual audit of the contact sheet once before the number is trusted.

**Run.**
```
.venv/Scripts/python.exe experiments/m2_exemplars/candidate.py
```
Outputs land in `experiments/m2_exemplars/out/` (gitignored).
