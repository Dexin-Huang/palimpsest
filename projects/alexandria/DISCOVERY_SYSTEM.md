# Discovery System

Purpose: define the smallest discovery system that can run unattended.

Palimpsest should not behave like a bespoke crawler for one archive.
It should behave like a boring machine:

`source adapter -> source refs -> DB ingest -> triage -> intake -> page packets`

The discovery side only succeeds if it stays simple enough to run hands-off.

## Core Rule

Do not rank sources only by fame.

Rank them by two things:

1. `north_star_fit`
   - does this source plausibly contain tracks of vanished human worlds?
   - stories, ritual, medicine, divination, cosmology, travel, local memory

2. `automation_fit`
   - can this source actually be processed without constant hand repair?
   - stable listing pages
   - stable document IDs
   - stable viewer or manifest URLs
   - public access
   - consistent page order
   - usable images

This is the real filter.

## Good Source Shape

The best source for Palimpsest is:

- digitized already
- public and scriptable
- thinly cataloged or underdescribed
- rich in manuscript images
- structurally regular enough to automate

That is why Vatican-style sources are strong:
- open IIIF
- stable manifests
- large shelves of underdescribed material

And why museum-highlight objects are weaker:
- often already interpreted
- often already selected as famous
- good for validation, worse for discovery

## Golden Path

The discovery system should stay small.

### 1. `discovery sources list`

Show registered sources and curated collections with:
- `automation_fit`
- `north_star_fit`
- access mode

### 2. `discovery sources scrape`

Return only `SourceDocumentRef` rows:
- source id
- collection
- shelfmark
- manuscript id
- manifest or viewer URL
- source catalog facts

No interpretation here.

### 3. `discovery sources ingest`

Write refs into the DB:
- preserve source facts
- optionally fetch manifests
- optionally triage immediately

### 4. `discovery triage`

Use the "Manuscript Paleontologist" prompt to decide:
- queue
- maybe
- skip

### 5. `library intake`

Only after triage:
- fetch page inventory
- create library record
- download pages

### 6. `page read`

Only after intake:
- prepare image
- create witness memo
- begin packet workflow

## Source-Fitness Heuristic

Use a 1-5 scale.

### `automation_fit`

- `5`: public, stable, IIIF-first, regular manifests, easy bulk intake
- `4`: public and regular, but some scraping or patching needed
- `3`: public but flaky or inconsistent in manifests / image delivery
- `2`: usable only with fragile scraping or frequent manual repair
- `1`: account-gated, unstable, or not suitable for unattended runs

### `north_star_fit`

- `5`: strong chance of ritual, medicine, divination, stories, cosmology, lived traces
- `4`: often useful, but mixed with commentary / reference material
- `3`: respectable but only occasional high-upside witnesses
- `2`: mostly already-read or low-texture material
- `1`: poor fit for the current thesis

## Current Source View

These are the current working assumptions.

### `vatican`

- `automation_fit = 5`
- `north_star_fit = 4`

Why:
- excellent manifests
- stable shelfmark structure
- many underdescribed witnesses
- especially good for shelfmark-thin bulk discovery

Weakness:
- lots of respectable but low-texture material
- novelty is not automatic

### `idp`

- `automation_fit = 3`
- `north_star_fit = 5` for curated ritual / medicine / Daoist lanes

Why:
- much stronger vanished-world content
- Dunhuang and Central Asian witness types are often exactly on thesis

Weakness:
- manifest and image delivery can be inconsistent
- not as frictionless as Vatican

This is why IDP is a good thematic source but not yet the cleanest autonomous one.

### `gallica`

- `automation_fit = 4`
- `north_star_fit = 4`

Why:
- public SRU search
- public ARK identifiers
- public IIIF manifests
- easier bulk scripting than many museum object portals

Weakness:
- richer metadata means less of the "shelfmark-thin mystery" effect
- queries still need care to avoid collapsing into printed books

## Source Expansion Rule

Do not add a new source adapter just because the repository is impressive.

Add it only if it meets at least one of these:

- `automation_fit >= 4`
- `north_star_fit >= 4`

Best case:
- both are high

If only one is high:
- keep the adapter curated and narrow

## Autonomous Operating Loop

The fully automatic version should look like this:

1. list candidate collections
2. scrape refs
3. ingest into DB
4. triage immediately
5. queue only high-score witnesses
6. intake and read them
7. promote strong packets to deeper scholarship

No human should need to babysit normal cases.

Humans should only step in for:
- source failures
- very high-value review pages
- prompt / policy redesign

## Simplicity Rule

The system should not need a different pipeline for every source.

Only three source-specific things should vary:
- listing scrape
- document ID normalization
- manifest or viewer extraction

Everything else should stay the same.

If an adapter needs much more than that, it is not clean enough yet.

## Minimal Commands

```bash
python -m palimpsest discovery sources list
python -m palimpsest discovery sources scrape --source vatican --collection Borg.cin --limit 10
python -m palimpsest discovery sources ingest --source idp --collection chinese_medicine --limit 10 --triage
python -m palimpsest discovery triage --collection Borg.cin --limit 20
```

## Practical Targeting

For the current north star:

- use Vatican-like sources for bulk unattended discovery
- use IDP-like sources for high-value thematic lanes
- avoid letting museum-highlight objects dominate the queue

The right mix is:

- `bulk source of underdescribed witnesses`
- `plus targeted thematic source of vivid vanished worlds`

That is the discovery machine we want.
