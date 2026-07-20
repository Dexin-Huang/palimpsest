# Palimpsest Vision

## Recover books that images have kept unread

Libraries have digitized vast manuscript collections, but a page photograph is
not yet a readable work. Text remains trapped behind damaged surfaces,
historical scripts, irregular layouts, inconsistent catalog data, translation
cost, and the editorial labor required to join fragments into a coherent book.

Palimpsest supplies that labor as an auditable production system.

## North star

Given an archival IIIF manifest, produce a book that a reader can use and a
scholar can inspect.

The book must be:

- **readable** — coherent chapters, translation, EPUB, and a hosted reader;
- **faithful** — diplomatic text remains distinct from reconstruction and
  emendation;
- **anchored** — page identity and image alignment survive downstream editing;
- **explainable** — interventions carry evidence and an apparatus;
- **reproducible** — recipes, prompts, models, parameters, and implementation
  identity are recorded;
- **operable** — work resumes safely, stale evidence reruns automatically, and
  paid configuration changes require explicit intent.

## Why a factory

Manuscript recovery is not one model call. It is a sequence of transformations
with different grains and failure modes. Image cleanup and reading happen per
page. Glossary construction, reconstruction, reference research, and
publication happen across the manuscript. Every step consumes a known artifact
and produces one new artifact.

A factory makes those boundaries concrete:

```text
manifest -> source records -> prepared pages -> transcription
         -> alignment -> translation -> reconstruction
         -> reference evidence -> emendation -> book -> EPUB + static reader
```

The line can therefore be parallel where work is independent, strict where
manuscript context is required, and resumable everywhere.

## Evidence before fluency

Modern models can generate fluent text more easily than trustworthy text.
Palimpsest reverses that priority. The diplomatic reading is preserved before
translation. Reconstruction records page joins. External research is bounded
and tied to manuscript anchors. Emendation sits beside the source reading rather
than replacing it. Publication exposes these layers instead of hiding them.

The aim is not merely a plausible edition. It is a useful edition whose claims
can be followed back to evidence.

## Durable system

Models and prompts will change. The system remains stable because artifact
contracts, recipe order, provenance, and publication boundaries do not depend
on one provider. Implementation identity is derived from executable source,
not manually assigned labels. A book's colophon records the actual production
inputs that made it.

Palimpsest should make adding a manuscript routine while keeping each published
book singular, inspectable, and worth reading.
