# entities/ (corpus-level canonicalization)

This folder is produced by merging per-page `claims[]`.

Recommended files (JSONL):
- persons.jsonl
- places.jsonl
- ships.jsonl

Each record should include:
- canonical id
- names/aliases
- time span (first_seen, last_seen)
- provenance: list of source spans that support it

You can keep this as "best effort" and rebuild it at any time from claims.