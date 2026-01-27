# events/ (corpus-level world model)

Universe reconstruction works best if you translate documents into **events**:

Examples:
- travel_license_granted
- embarkation
- departure
- arrival (if present)
- ship_manifest_created

Each event should have:
- event_id
- event_type
- date (or date_range)
- place (canonical place_id + raw string)
- participants (person_ids + roles)
- source_spans (page_id + zone_id + char ranges)
- confidence

Store as `events.jsonl`.