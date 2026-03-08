# Product Focus

Purpose: define the current Palimpsest product wedge and explicitly defer
everything else.

---

## 1. Current Product

Palimpsest's first real product is:

`evidence-bound diplomatic restoration of historical pages, plus a readable book view`

That means the system should do one thing exceptionally well:
- take archival page images
- reconstruct the page faithfully enough to trust
- produce a readable edition derived from that faithful reconstruction

This is not "generic OCR."
This is not "knowledge graph first."
This is not "crawl every archive first."

Those may matter later. They are not the current product.

---

## 2. Why This Comes First

Everything else depends on restoration quality.

If the restoration is weak:
- readable editions drift away from the witness
- extraction becomes untrustworthy
- comparison across manuscripts becomes noisy
- research conclusions become brittle

If the restoration is strong:
- readable editions are easy to derive
- downstream extraction has a stable evidence base
- scholars can inspect and correct the real object
- the system produces value even before full automation exists

---

## 3. Golden Path

The current golden path is:

`intake -> image prep -> page typing -> transcription -> canonical.page -> diplomatic restoration -> readable edition`

The internal truth object is:

`canonical.page`

The first serious output is:

`diplomatic restoration`

The first human-facing output is:

`readable edition`

---

## 4. What "Diplomatic Restoration" Means

The product is not merely linearly readable text.

It should preserve:
- line breaks where meaningful
- column structure
- marginalia and interlinear insertions
- rubrication and initials when possible
- uncertain readings
- provenance back to page zones and spans

The result should be good enough that a scholar can say:

`this reconstructed page still behaves like the original witness`

---

## 5. What Gets Deferred

For now, these are secondary:
- broad discovery automation
- mass ingestion of every available collection
- cross-manuscript comparison
- claim graphs and knowledge graphs
- large-scale agentic research workflows
- polished public interfaces

They are deferred, not rejected.
They only matter after restoration quality is reliable.

---

## 6. Bootstrap Strategy

The system should bootstrap manually first.

That means:
- hand-pick a small set of strong manuscripts
- process a limited number of pages deeply
- build a small gold or near-gold evaluation set
- compare model, prompt, and prep choices against that set
- improve the quality/cost curve before scaling out

The goal is not early automation everywhere.
The goal is learning where quality actually comes from.

---

## 7. Quality / Cost Strategy

Optimize for the best restoration quality per dollar, not the lowest raw model
cost.

The practical loop is:
1. classify page type cheaply
2. avoid spending on pages that are blank, decorative, or low-value
3. run the best restoration path on high-value pages
4. keep strong provenance and audit artifacts
5. measure failure modes before scaling volume

Likely operating pattern:
- strong frontier model for benchmarking, pseudo-gold, and hard pages
- cheaper or local specialists later for bulk pages
- human review on the pages that matter most

---

## 8. What Success Looks Like

Palimpsest is on the right path when it can do this repeatedly:
- ingest a manuscript
- produce trustworthy restored pages
- assemble them into a readable book
- let a human move through the manuscript without losing contact with the
  original witness

Only after that should the system expand into broader knowledge extraction and
large-scale discovery.

---

## 9. Immediate Design Priorities

In order:
1. `canonical.page`
2. diplomatic restoration output contract
3. readable edition assembly
4. restoration evaluation workflow
5. routing and cost control

Everything else is downstream.

See:
- `docs/PAGE_EVIDENCE_SCHEMA.md`
- `docs/DIPLOMATIC_RESTORATION_CONTRACT.md`
