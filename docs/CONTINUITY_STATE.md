# Continuity State

Purpose: keep manuscript reading contiguous across stateless sessions without
reloading the entire codex.

The continuity layer sits above `page.packet` and below manuscript-wide dossier
work.

Core idea:

- `page.packet` stores the local scholarly work for one page/spread
- `page.handoff` carries a tiny forward-facing state to the next page
- `window.synthesis` stabilizes meaning across a small contiguous run

This is better than a pure linked list because it preserves sequence and local
compression at the same time.

## 1. Hot / Warm / Cold Context

### Hot

Load by default for the next page:

- current `page.packet`
- previous `page.handoff`
- current `window.synthesis` if one exists

### Warm

Load only when needed:

- previous page packet interpretation
- previous page packet terms/questions
- current section synthesis

### Cold

Keep on disk unless explicitly needed:

- older packets
- older windows
- full manuscript dossier

## 2. `page.handoff`

`page.handoff` is the forward memory from page `i` to page `i+1`.

It should contain:

- short summary of what just happened
- active entities likely to recur
- active terms likely to recur
- questions the next page can confirm or revise
- continuity links to previous/next pages

It should not:

- duplicate the full packet
- become an essay
- pretend uncertain continuity is already confirmed

CLI:

```bash
python -m palimpsest page handoff \
  --packet library/<doc_id>/experiments/<page>_packet/packet.json \
  --next-page-id <next_page_id>
```

Outputs:

- `page_handoff.json`
- `page_handoff.md`
- `page_handoff_meta.json`

## 3. `window.synthesis`

`window.synthesis` is a small sliding synthesis across adjacent pages.

Recommended window:

- usually `3` packets
- `2` packets is acceptable early in a run

It should answer:

- what argument or narrative thread is clearly contiguous here?
- which names/terms have stabilized across the window?
- what should the next page confirm or revise?

CLI:

```bash
python -m palimpsest page window \
  --packet library/<doc_id>/experiments/<page1>_packet/packet.json \
  --packet library/<doc_id>/experiments/<page2>_packet/packet.json \
  --packet library/<doc_id>/experiments/<page3>_packet/packet.json
```

Outputs:

- `window_synthesis.json`
- `window_synthesis.md`
- `window_synthesis_meta.json`

## 4. Recommended Workflow

1. build `page.packet`
   - attach `previous_handoff_path` and `window_synthesis_path` when available
2. fill witness
3. annotate / translate / interpret
4. render edition
5. write `page.handoff`
6. once 2-3 packets exist, write `window.synthesis`
7. keep moving front to back

That gives:

- local packet truth
- cheap forward continuity
- periodic compression of nearby pages

The packet should carry these continuity references in `packet.json`, so the
dedicated scholar workflow can reload the hot context automatically.

## 5. Why This Is Efficient

The next session does not need the whole manuscript.

It only needs:

- previous handoff
- current packet
- current window synthesis

That is enough to preserve contiguity while keeping context small.
