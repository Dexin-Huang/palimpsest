# `f004r` Benchmark

This is the first fixed benchmark page for region-first reconstruction.

Why this page matters:

- It has one header and one main-text block on each side of a spread.
- It has Latin marginalia overlapping the right main-text zone.
- It has a page number box and a modern footer that should not pollute the witness.
- It exposes the exact boundary mistakes we care about:
  - header spill into main text
  - main-text spill into marginalia
  - page-number noise

Current benchmark contract:

- Machine-readable expectations live in [benchmark.json](D:\Projects\palimpsest\benchmarks\pages\vatican_borg_cin_361\f004r\benchmark.json).
- Live working packet is [packet.json](D:\Projects\palimpsest\library\vatican_borg_cin_361\experiments\f004r_packet_v1\packet.json).
- Current layout artifacts live under [layout_probe](D:\Projects\palimpsest\library\vatican_borg_cin_361\experiments\f004r_packet_v1\layout_probe).

How to use it:

1. Regenerate `f004r` through the canonical path.
2. Compare the resulting `section_resolution`, `box_cleanup`, and `page_assembly` against `benchmark.json`.
3. Only fan changes out to the full set after `f004r` passes.

This page is meant to be iterated on repeatedly. If the pipeline cannot get `f004r` mostly right, it is not ready for unattended scale.
