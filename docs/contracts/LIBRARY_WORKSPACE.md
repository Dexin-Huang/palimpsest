# Library Workspace Contract

Owner: library

Source constants: `palimpsest/contracts.py`

## Canonical Layout

Directory: `library/<doc_id>/`

Required files:
- `metadata.json`
- `page_list.json`

Required directories:
- `images/`
- `experiments/`
- `runs/`

Repository-level registry:
- `library/index.jsonl`

## Semantics

- `metadata.json` is the canonical document metadata record for one library item.
- `page_list.json` is the canonical page ordering and page-id list for downstream packet and reader workflows.
- `experiments/` holds page-scoped runtime workspaces, including packet workspaces named `<page_id>_packet_v1/`.

## Notes

- `images_cleaned/` is an optional derived image directory, not a required contract surface.
- Intake and metadata updates should write through the library-owned paths defined in `palimpsest/contracts.py`.
