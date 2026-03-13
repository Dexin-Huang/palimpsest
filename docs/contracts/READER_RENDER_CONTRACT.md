# Reader Render Contract

Owner: reader

Source constants: `palimpsest/contracts.py`

## Inputs

Required inputs:
- `packet.json`
- `page_assembly.json` referenced by the packet

Optional inputs referenced from packet state:
- `witness.md`
- `translation.md`
- `interpretation.md`
- `notes.md`
- `terms.md`
- `questions.md`

## Outputs

Canonical render outputs:
- `index.html`
- `render.json`
- `render_meta.json`

## Semantics

- reader render is a downstream transformation from packet plus assembly inputs into HTML and structured render JSON.
- `render_meta.json` records render context such as packet path, output paths, image href, title, and whether the target directory is the packet workspace.
- site builds may render into external output folders; those renders must stay read-only with respect to packet state.

## Notes

- packet-state sync for `index.html` and `render.json` belongs in packet/page workflow code, not in `palimpsest/reader/folio.py`.
