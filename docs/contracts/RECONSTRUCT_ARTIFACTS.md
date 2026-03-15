# Reconstruct Artifact Contract

Owner: reconstruct

Source constants: `palimpsest/contracts.py`

## Probe Workspace

The reconstruct lane writes a probe workspace, either as a standalone experiment directory such as `<page_id>_layout_probe/` or inside a packet workspace under `layout_probe/`.

Canonical artifact filenames:
- `layout_probe.json`
- `layout_overlay.png`
- `region_reads.json`
- `section_resolution.json`
- `box_cleanup.json`
- `page_validation.json`
- `page_validation.md`
- `page_assembly.json`
- `page_assembly.md`

Canonical meta sidecars:
- `layout_probe_meta.json`
- `region_reads_meta.json`
- `section_resolution_meta.json`
- `box_cleanup_meta.json`
- `page_validation_meta.json`
- `page_assembly_meta.json`

## Supporting Outputs

Additional reconstruct outputs:
- probe prompt copy: `layout_prompt.txt`
- probe raw response: `layout_probe_raw.json`
- probe crops directory: `crops/`
- prepared image workspace suffix: `_prepared`
- prepared image metadata: `prepare_meta.json`
- reading workspace suffix: `_reading`
- page reading metadata: `reading_meta.json`
- section synthesis directory: `section_synthesis/`
- section synthesis outputs: `section_synthesis.md`, `section_synthesis_meta.json`, `inputs.md`

## Semantics

- `layout_probe.json` is the authoritative coarse region layout for downstream reconstruction.
- `region_reads.json` is the per-region witness read set.
- `section_resolution.json` assigns canonical witness ownership per region.
- `box_cleanup.json` records targeted pairwise repair decisions.
- `page_validation.json` records structural QA findings over the assembled page.
- `page_assembly.json` is the deterministic downstream artifact packets and reader consume.

## Notes

- New code should resolve these filenames through `palimpsest/contracts.py` instead of retyping string literals.
