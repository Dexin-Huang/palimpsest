# Translation Brief Schema

The frozen artifact produced by the survey phase and consumed by all translators.

## Fields

```json
{
  "version": 1,
  "document": {
    "title": "Codice miscellaneo (medicina)",
    "shelfmark": "Pal.lat.1199",
    "date": "1350-1400",
    "language": "Latin",
    "script_notes": "Heavy scholastic abbreviations, some damaged folios"
  },
  "outline": [
    {"start_page": "f002r", "end_page": "f006v", "section": "Medical recipes (head to toe)"},
    {"start_page": "f007v", "end_page": "f011r", "section": "Phlebotomy guides"},
    {"start_page": "f014r", "end_page": "f037v", "section": "Problemata de corpore humano"}
  ],
  "glossary": [
    {"term": "complexio", "translation": "temperament", "note": "humoral constitution, not modern 'complexion'"},
    {"term": "calor naturalis", "translation": "natural heat", "note": "Galenic vital heat concept"},
    {"term": "spiritus animalis", "translation": "animal spirit", "note": "keep Latin sense, not modern 'spirit'"}
  ],
  "abbreviation_policy": [
    {"abbrev": "scdm", "expansion": "secundum"},
    {"abbrev": ".i.", "expansion": "id est"},
    {"abbrev": "q", "expansion": "quod / quae (context-dependent)"}
  ],
  "style_rules": [
    "Literal translation preferred over interpretive",
    "Keep Latin terms in parentheses on first occurrence: 'temperament (complexio)'",
    "Preserve paragraph structure from source",
    "Mark uncertain readings with [?]",
    "Do not modernize medical terminology"
  ],
  "named_entities": [
    {"name": "Galenus", "translation": "Galen"},
    {"name": "Avicenna", "translation": "Avicenna (Ibn Sina)"},
    {"name": "Cancellarius Montispessulani", "translation": "the Chancellor of Montpellier"}
  ],
  "difficulty_flags": [
    {"page_id": "f034r", "issue": "VLM transcription failure — repeated 'q' characters"},
    {"page_id": "f001v", "issue": "Near-illegible, heavily abbreviated"}
  ]
}
```
