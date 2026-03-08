# Reading Prompts

Palimpsest needs a separate witness lane and synthesis lane.

- `witness` prompts stay close to a single page
- `synthesis` prompts use multiple pages to build translation and interpretation

Current page-level default:

- `palimpsest/prompts/page_witness_focused.txt`

Current cross-page synthesis prompt:

- `palimpsest/prompts/section_synthesis_focused.txt`

Current model recommendation:

- `gemini-3-flash-preview` for serious page reading
- `gemini-3.1-flash-lite-preview` only for cheap scouting or support passes

Design principles:

- witness text first
- page-level outputs should be narrow and local
- translation and interpretation should move up to a multi-page section pass
- evidence before interpretation
- explicit uncertainty

Recommended use:

1. Prepare each page down to its manuscript-bearing area.
2. Run the witness-focused prompt on each prepared page image.
3. Save those page memos as local witness artifacts.
4. Feed multiple page memos or witnesses into a synthesis prompt for translation and interpretation.
5. Only after that, derive structured notes or research claims.

The CLI does the preparation step automatically during `page read` unless
`--raw` is passed.

Preparation principles:

- deterministic first
- remove viewer footer and obvious dead margins
- keep the manuscript area intact
- prefer one good crop over a complicated region graph

CLI sketch:

```bash
python -m palimpsest page prepare --image library/<doc_id>/images/<page>.jpg
python -m palimpsest page read --image library/<doc_id>/images/<page>.jpg
python -m palimpsest page synthesize \
  --input library/<doc_id>/experiments/<page1>_reading/<page1>_reading.md \
  --input library/<doc_id>/experiments/<page2>_reading/<page2>_reading.md \
  --input library/<doc_id>/experiments/<page3>_reading/<page3>_reading.md
```

The witness lane is deliberately narrower than the synthesis lane.
Single-page prompts should avoid broad interpretation when the surrounding folios
are missing.
Translation and historical interpretation should use a broader local context.

Related:

- `docs/MODEL_STRATEGY.md`
