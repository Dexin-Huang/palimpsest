# Translation Pipeline: A/B Test

Two competing approaches to consistent book-level manuscript translation.

## Approach A: Deterministic batch pipeline (Codex recommendation)

Three-pass pipeline using plain async Python + API calls:

1. **Survey pass** — parallel workers scan chunks, produce a `translation_brief` (glossary + outline + style rules + abbreviation policy + entity register + ambiguity notes)
2. **Translation pass** — parallel workers translate chunks with overlap (2-3 pages context on each side), consulting the frozen brief. Each translator flags: `starts_mid_sentence`, `ends_mid_sentence`, `needs_term_reconcile`
3. **Repair pass** — targeted fixes on flagged spans and chunk boundaries only

Key: separate chunk planning from concurrency. Schema-validated JSONL per phase. Same pattern as existing transcribe.py/enrich.py.

## Approach B: Multi-agent team (Claude Agent SDK)

Four-role agent team with shared state:

1. **Parallel scouts** — N agents survey N chunks, produce partial glossaries + section notes
2. **Lexicographer** — merges partial glossaries, resolves conflicts, produces frozen glossary v1
3. **Parallel translators** — N agents translate N chunks with glossary + section context
4. **Reviewer** — reads batch boundaries, fixes inconsistencies

Key: agents can discuss terms, propose alternatives, iterate on wording. The glossary grows through agent negotiation, not just extraction.

## Test plan

- Run both on Pal.lat.1199 (110 pages, medieval Latin medical miscellany)
- Compare: term consistency across the book, translation quality, boundary handling, cost, latency
- Human evaluator spot-checks 10 pages from each approach

## Decision criteria

- Quality at boundaries (split sentences, term drift)
- Glossary coverage and consistency
- Cost per page
- Wall-clock time for 100 pages
- Code complexity / maintainability

## Status

Design phase. Neither built yet. Existing `enrich.py` is the naive page-by-page baseline.
