# Source Adapters

Purpose: define the intake edge of Palimpsest's "virtual Library of Alexandria."

The goal is not to hard-code one archive at a time forever. The goal is to add
small, clean adapters for digitized sources that already expose page images but
do not meaningfully surface their contents.

## Core Idea

Palimpsest should treat external repositories as sources of evidence, not as
the place where understanding stops.

Each source adapter should do only three things:

1. list collections or search surfaces
2. produce manuscript references with stable IDs and manifest/viewer links
3. hand off to the normal IIIF / intake pipeline

That is enough to build a fully virtual "Library of Alexandria":
- Vatican
- British Library / IDP
- National Library of China
- Endangered Archives Programme
- Sinai projects
- any other repository with digitized page images

## Source Fitness

Not all digitized repositories are equally useful.

Palimpsest should prefer sources that score well on two axes:

1. `automation_fit`
   - can we scrape and process the source unattended?
   - stable IDs
   - stable manifests or image URLs
   - public access
   - regular page structure

2. `north_star_fit`
   - does the source likely contain vanished human worlds?
   - stories, ritual, medicine, divination, cosmology, travel, local memory

The best source is not merely famous.
It is:
- machine-friendly
- underdescribed
- full of material people have not really read

### Working rule

- `automation_fit >= 4` means the source is good for bulk unattended crawling
- `north_star_fit >= 4` means the source is thematically rich enough to justify effort
- if a source scores high on only one axis, keep it curated and narrow

## Minimal Primitives

The adapter layer should stay tiny.

### `SourceCollection`

One browseable collection inside a source.

Fields:
- `source_id`
- `key`
- `label`
- `listing_url`
- `notes`
- `automation_fit`
- `north_star_fit`
- `access`

### `SourceDocumentRef`

One manuscript reference discovered from a source.

Fields:
- `source_id`
- `collection`
- `shelfmark`
- `manuscript_id`
- `manifest_url`
- `viewer_url`
- `thumbnail_url`
- `quality`
- `source_catalog`
- `extra`

### `DiscoverySourceAdapter`

One source implementation.

Methods:
- `list_collections()`
- `scrape_collection(collection, **kwargs)`

That is all.

## Design Rule

The adapter should not try to understand the manuscript.

It should only:
- expose what the source says
- preserve source-facing facts
- avoid inventing metadata

Interpretation belongs later in:
- triage
- witness extraction
- packet scholarship

## Current Implementation

Current adapters:
- `VaticanSourceAdapter`
- `IDPSourceAdapter`

Code:
- `palimpsest/discovery/sources/adapter.py`
- `palimpsest/discovery/sources/registry.py`
- `palimpsest/discovery/sources/vatican_adapter.py`
- `palimpsest/discovery/sources/idp_adapter.py`

The Vatican adapter wraps the existing Vatican listing scraper and keeps the
rest of the system unchanged.

The IDP adapter is intentionally curated. It does not attempt to crawl the full
IDP universe. It exposes a few high-signal search surfaces aligned with the
north star:
- `stein_dunhuang_chinese`
- `chinese_astronomy_divination`
- `chinese_medicine`
- `chinese_magic`
- `chinese_prayers`
- `chinese_daoism`

Each of those is just a stable faceted collection URL plus shallow record
scraping and IIIF-manifest extraction.

Current fit assumptions:

- `vatican`
  - `automation_fit = 5`
  - `north_star_fit = 4`
  - best for bulk shelfmark-thin discovery

- `idp`
  - `automation_fit = 3`
  - `north_star_fit = 5` in curated medicine / magic / Daoist lanes
  - best for vivid thematic material, not yet the cleanest unattended source

## Target Expansion

Best next adapter targets for the current north star:

1. `idp`
   - Dunhuang / Chinese / Central Asian manuscript corpora
   - strong fit for stories, ritual, divination, lived traces

2. `eap`
   - Endangered Archives Programme
   - strong fit for local memory, ritual, medicine, non-canonical practice

3. `bl_asian`
   - British Library Asian collections where IIIF exists

4. `nlc`
   - National Library of China surfaces when stable manifests or image APIs are available

5. `vatican-like open IIIF repositories`
   - repositories with shelfmark-level listing pages and stable public manifests
   - these are often better autonomous sources than museum object portals

6. `gallica`
   - strong automation potential because of public IIIF at scale
   - likely best for bulk manuscript intake once rate limits are handled

7. `vhmml`
   - high thematic upside, but lower unattended suitability because access and reuse
     constraints are tighter

## Selection Rule

Do not add adapters just because the source is famous.

Add them when the source is likely to contain:
- underdescribed manuscript images
- thin metadata
- material aligned with the north star:
  stories, mysticism, medicine, cosmology, ritual, travel, local memory
- and enough structural regularity to run unattended

## Practical Next Step

Once a source adapter exists, the normal workflow should be:

`adapter -> source refs -> manifest fetch -> intake -> triage -> page read`

Minimal CLI:
- `python -m palimpsest discovery sources list`
- `python -m palimpsest discovery sources scrape --source idp --collection chinese_magic --limit 5`
- `python -m palimpsest discovery sources ingest --source idp --collection chinese_daoism --limit 5 --triage`

`discovery sources list` should be treated as the source radar:
- look for high `automation_fit`
- look for high `north_star_fit`
- queue the intersection first

The adapter layer should remain boring.
That is what makes the rest of the system elegant.
