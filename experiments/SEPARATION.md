# The separation protocol

Standing goal: the best possible **unsupervised character separation** —
every character on every folio located and lassoed from the ink alone, no
transcription in the loop. Separation is the foundation everything above
it stands on (clustering, labeling, exemplars, hand fonts, binding-as-
cryptogram); an error here is amplified by every later stage.

## Why unsupervised is the right constraint

Locations need only images. That means the entire digitized corpus —
tens of thousands of Dunhuang folios that have never been transcribed —
is usable *data* for this problem, not just its eventual target. The
transcription, where it exists, is demoted to an evaluation signal.

## Data tiers

- **T0 (now)**: P.3477, 3 folios. Has transcription → count-consistency
  checks; has the audited overlay and 141-entry ground-truth material.
- **T1 (grow)**: additional Gallica Pelliot chinois scrolls, images only,
  ingested via the existing intake/download line. Target: 10+ folios
  spanning clean sutra hands, damage, glosses, semi-cursive.
- **Fixtures**: frozen page images per folio; every candidate runs on ALL
  of them, always. New folios join the fixture set on arrival.

## Metrics — the scoreboard, per folio

1. **Count consistency** (where transcription exists): per-column
  |cells − chars| / chars, aggregated. A proxy, not truth — but free and
  corpus-wide.
2. **Junk rate**: fraction of cells junk-gated. Rising junk = leaky
  segmentation; falling junk with rising counts = real improvement.
3. **Audited separation rate** (the real number): on sampled columns,
  each cell hand-verdicted `correct | split | merge | ghost | miss` —
  the labels.csv pattern, extended to separation. Grows into the gold set.
4. **Cluster-purity uplift** (downstream signal): better separation →
  cleaner unsupervised clusters. Run the cluster pass on every candidate's
  output; purer sheets = structural evidence, no labels needed.
5. **Visual audit — mandatory**: no promotion without eyes on the overlay
  of the *worst* folio. The "100% boxed" mirage is the standing reason.

## The loop

candidate → run on ALL fixture folios → scoreboard + overlays →
visual audit on worst folio → promote in place or delete → LOG entry.

## Ladder — current standings

| Rung | Cost | Status |
|---|---|---|
| CV incumbent (projection + DTW + refine + lasso) | zero | champion; 689 cells vs ~607 on p1 |
| GrabCut, mask-seeded hybrid | zero | next challenger — faint-char recovery |
| Marker watershed for touching chars | zero | staged |
| MobileSAM/EdgeSAM via onnxruntime | ~50MB | only if zero-byte rungs stall |
| Tiny trained detector (synthetic hand-font data + gold set) | training | end state at corpus scale |

## Standing rules

- Zero-byte rungs before downloads; downloads before training.
- Every constant in a candidate is visible and named; tuning happens in
  the open, on the fixtures.
- Failures are findings: log them with numbers, delete the code.
- The protocol file itself is versioned; git is the history.
