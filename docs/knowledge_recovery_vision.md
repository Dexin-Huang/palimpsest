# Knowledge Recovery Vision

## Core Thesis

Human societies have generated far more knowledge than they have preserved in
living use. Most knowledge traditions were local, expensive to transmit, and
fragile across wars, migrations, religious change, language shift, and simple
neglect. The result is not just "missing documents"; it is the repeated loss of
techniques, explanations, stories, and conceptual systems that once mattered.

Palimpsest should be built around a larger goal than cheap transcription:

**Palimpsest is a discovery engine for lost or neglected knowledge traditions.**

The point is not merely to OCR archives. The point is to recover low-attention
parts of civilization that were never worth enough human labor to process at
scale until now.

Examples:
- alchemy and proto-chemistry
- astronomy, calendrics, and cosmology
- medicine, pharmacology, and recipe books
- maps, itineraries, and geographic description
- chronicles, myths, and transmission of stories
- technical diagrams, tables, and marginal commentary
- local scientific, ritual, or craft traditions that later fell out of fashion

## Why Now

Historically, archives were constrained by labor economics:
- experts were rare
- paleography was slow
- translation was slower
- unusual domains had tiny audiences
- many corpora had no clear commercial or academic ROI

AI changes that constraint. If page images can be transcribed, translated,
normalized, compared, and searched at scale, then the bottleneck shifts from
"nobody can afford to read this corpus" to "which corpus is worth exploring
next?"

This matters because civilization is path dependent and lossy. Human life is
optimized for local adaptation, not for perfect long-term preservation. Tribal
knowledge disappears. Scholarly fashions shift. Entire domains become
disreputable or unfunded. Yet those abandoned domains may still contain:
- accurate observations hidden inside obsolete theories
- forgotten engineering or medical practices
- early versions of ideas later rediscovered elsewhere
- transmission links between regions and periods
- contradictions that change accepted intellectual history

## What Palimpsest Should Optimize For

If the goal is knowledge recovery, Palimpsest should not be designed as a
generic OCR conveyor belt. It should optimize for:

- high-recall discovery of unusual or under-studied material
- strong provenance on every extracted claim
- multilingual and cross-script comparison
- uncertainty tracking rather than false certainty
- cross-document alignment across centuries and traditions
- human-reviewable hypotheses instead of confident narrative synthesis

This means transcription is necessary, but it is not the product. It is the
evidence layer.

## Product Thesis

The stronger product thesis is:

**Palimpsest turns neglected archives into searchable evidence for discovering
lost knowledge.**

The system should support a workflow like:

1. Discovery
   - monitor new releases and catalog updates
   - rank collections by novelty, obscurity, and likely knowledge density

2. Evidence capture
   - transcribe pages
   - normalize and translate text
   - preserve page- and zone-level provenance

3. Knowledge extraction
   - identify entities, substances, instruments, places, texts, diagrams, and
     technical terms
   - extract claims, procedures, recipes, observations, and references

4. Comparative analysis
   - align similar ideas across manuscripts, languages, and centuries
   - surface repeated motifs and contradictions
   - trace transmission chains and conceptual inheritance

5. Research assistance
   - propose hypotheses
   - point scholars back to source pages
   - never separate conclusions from evidence

## Research Targets

Promising early target domains:

- Latin alchemy and natural philosophy
- medieval and early modern astronomy / computus
- pharmacology, herbals, and recipe books
- maps, travelogues, and geographic compendia
- technical miscellanies and encyclopedic compilations
- commentaries with dense marginalia

These domains are attractive because they combine:
- high intellectual value
- low historical processing coverage
- structured recurring concepts
- cross-manuscript comparability

## Why Chinese Corpora Matter

Ancient and premodern Chinese corpora deserve special attention.

Reasons:
- long printing traditions created larger surviving text corpora earlier than in
  many other regions
- standardized written forms increase the chance of cross-document alignment at
  scale
- commentarial traditions preserve chains of interpretation, not just isolated
  texts
- technical, medical, astronomical, geographic, and bureaucratic works often
  survive in large quantities
- even when a tradition is well known in the abstract, the long tail of
  specific editions, local compilations, annotations, and diagrams remains
  underexplored

This does not make Chinese sources "easy." Layout, print quality, historical
vocabulary, variant glyphs, and domain knowledge still matter. But it does make
them especially valuable for a system built around large-scale comparison,
transmission analysis, and recovery of neglected intellectual content.

Palimpsest should therefore remain open to multiple civilizational lanes rather
than constraining itself to a single institutional archive or language family.

## Design Implications

If this vision is correct, several design consequences follow.

### 1) Discovery is first-class

The system should rank collections not just by beauty or completeness, but by:
- obscurity
- under-studied subject matter
- presence of diagrams, tables, recipes, or glosses
- signals of scientific, technical, cosmological, or geographic content
- availability of enough material for comparison

### 2) Provenance is non-negotiable

AI does not automatically "understand the past." Without provenance, it only
produces plausible modern stories about old texts.

Every claim should point back to:
- document
- page
- zone or line
- transcription version
- model or parser responsible
- confidence / disagreement signals

### 3) Canonical outputs should remain evidence-first

Per-page JSON is still the right canonical layer, but it should support more
than plain transcription:
- diplomatic text
- normalized text
- translation
- page type and layout
- entities
- claims
- references
- provenance spans

### 4) The system needs hypothesis support, not just exports

Palimpsest should eventually help answer questions like:
- Where does this recipe first appear?
- Which manuscripts repeat this instrument description?
- Is this "alchemy" passage actually practical metallurgy?
- Which map traditions share the same place ordering?
- Which later summaries contradict the earlier source witnesses?

## Non-Goals

This vision does not imply:
- replacing scholars with autonomous historical interpretation
- treating every extracted pattern as true
- collapsing all domains into one generic model and prompt
- optimizing only for throughput at the expense of auditability

## Strategic Focus

The practical strategy is:
- start with one or two high-upside domains
- build the evidence pipeline well
- add extraction and comparison layers
- use AI to widen the searchable frontier of human history

In short:

**Palimpsest should help recover the neglected intellectual history of
civilization, not just transcribe documents.**
