# Alexandria

Universal discovery index for digitized manuscripts across all major repositories worldwide.

## Vision

A searchable catalog spanning every IIIF-publishing institution globally — Vatican, Gallica/BnF, IDP/British Library, Bodleian, e-codices, BSB, Harvard Yenching, National Diet Library, and more. Not downloading everything — knowing where everything *is*, then selectively intaking and transcribing.

Closest existing effort: Biblissima (~65k manuscripts, European-only, no East Asian coverage, no transcription pipeline). Alexandria fills the gap by adding East Asian repositories and plugging into palimpsest's VLM transcription pipeline.

## Status

Early design. Source adapter system exists for Vatican, Gallica, and IDP. See design docs in this directory.

## Design Docs

- [DISCOVERY_SYSTEM.md](DISCOVERY_SYSTEM.md) — Core discovery architecture
- [SOURCE_ADAPTERS.md](SOURCE_ADAPTERS.md) — Repository connector design
- [discovery_strategy.md](discovery_strategy.md) — Manuscript selection strategy
- [FACTORY.md](FACTORY.md) — Discovery + triage pipeline
