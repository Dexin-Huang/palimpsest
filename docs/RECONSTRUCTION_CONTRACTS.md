# Reconstruction Contracts

This file defines the concrete JSON contracts for the first two reconstruction
primitives:

1. `page.route`
2. `page.score`

The point is to keep the hard-page lane small, explicit, and testable.

## `page.route`

Purpose:
- decide whether a page should go through direct transcription, split/merge, or
  the full workspace loop

Artifact:
- `page.route`

Default file path:
- `library/<doc_id>/exports/routes/<page_id>.json`

Current CLI:

```bash
python -m palimpsest page route --image library/<doc_id>/images/<page>.jpg
python -m palimpsest page route --image library/<doc_id>/images/<page>.jpg --scout-json path/to/<page>_pass1.json
```

Canonical fields:
- `artifact_type`
- `created_at`
- `doc_id`
- `page_id`
- `image_path`
- `difficulty`
- `recommended_path`
- `cv_score`
- `scout_score`
- `overall_score`
- `cv`
- `scout`
- `reasons`
- `notes`

Difficulty values:
- `easy`
- `medium`
- `hard`
- `blocked`

Recommended path values:
- `direct_transcription`
- `split_merge`
- `workspace_loop`
- `human_review`

Current `cv` fields:
- `ink_ratio`
- `contrast_score`
- `writing_area_ratio`
- `footer_ratio`
- `row_density_mean`
- `row_density_std`
- `line_density_estimate`
- `column_count_estimate`
- `layout_complexity`

The route should stay cheap. It is mostly a local CV decision with optional
scout-pass health folded in.

Example:

```json
{
  "artifact_type": "page.route",
  "created_at": "2026-03-07T18:00:00Z",
  "doc_id": "vatican_borg_cin_361",
  "page_id": "f200r",
  "image_path": "D:/Projects/palimpsest/library/vatican_borg_cin_361/images/f200r.jpg",
  "difficulty": "hard",
  "recommended_path": "workspace_loop",
  "cv_score": 63.7,
  "overall_score": 63.7,
  "cv": {
    "ink_ratio": 0.0912,
    "contrast_score": 0.41,
    "writing_area_ratio": 0.52,
    "footer_ratio": 0.18,
    "row_density_mean": 0.12,
    "row_density_std": 0.05,
    "line_density_estimate": 31,
    "column_count_estimate": 1,
    "layout_complexity": 0.59
  },
  "reasons": [
    {
      "code": "high_line_density",
      "severity": "warning",
      "note": "Estimated 31 line groups."
    }
  ],
  "notes": [
    "Use the persistent workspace loop and score local issues."
  ]
}
```

## `page.score`

Purpose:
- measure whether the current page state improved

Artifact:
- `page.score`

Default file path:
- `experiments/<page>_workspace/scores/<page>_machine_score.json`

Current CLI:

```bash
python -m palimpsest page score --workspace library/<doc_id>/experiments/<page>_workspace/workspace.json
```

Canonical fields:
- `artifact_type`
- `created_at`
- `score_type`
- `formula_version`
- `doc_id`
- `page_id`
- `source_artifact_type`
- `source_artifact_path`
- `global_score`
- `component_scores`
- `penalties`
- `issue_scores`
- `judge_decisions`
- `notes`

Current score types:
- `machine`
- `gold`

The first working lane only computes `machine`.

Important:
- `machine` is a structural proxy, not witness truth
- use it to compare local assembly states
- do not confuse it with a scholar-grade fidelity judgment

Current machine components:
- `output_validity`
- `duplicate_cleanliness`
- `overlap_agreement`
- `rerun_consistency`
- `term_spelling_consistency`
- `boundary_completeness`

Current penalties:
- missing or invalid crop outputs
- merge missing
- heavy overlap duplication
- weak crop boundaries

`issue_scores` are local and actionable. They should point the controller at the
next repair target:
- one issue per crop validity problem
- one issue per crop boundary
- one page-level duplication issue

Example:

```json
{
  "artifact_type": "page.score",
  "created_at": "2026-03-07T18:03:00Z",
  "score_type": "machine",
  "formula_version": "page-score.v1",
  "doc_id": "vatican_borg_cin_361",
  "page_id": "f200r",
  "source_artifact_type": "workspace.smoothed_text",
  "source_artifact_path": "smoothed/f200r_smoothed_diplomatic.txt",
  "global_score": 58.42,
  "component_scores": [
    {
      "name": "output_validity",
      "value": 1.0,
      "weight": 0.35,
      "observed": true
    },
    {
      "name": "overlap_agreement",
      "value": 0.61,
      "weight": 0.2,
      "observed": true
    }
  ],
  "penalties": [
    {
      "code": "weak_boundary",
      "amount": 5.0,
      "note": "Weak crop boundary between f200r_band2 and f200r_band3."
    }
  ],
  "issue_scores": [
    {
      "issue_id": "boundary:f200r_band2->f200r_band3",
      "kind": "boundary_overlap",
      "value": 0.34,
      "status": "open"
    }
  ],
  "judge_decisions": [],
  "notes": [
    "Scored assembled text from smoothed/f200r_smoothed_diplomatic.txt."
  ]
}
```

## Design Rules

- `page.route` should be cheap enough to run on every page.
- `page.score` should be stable enough to reject noisy agent churn.
- both artifacts should be machine-readable first
- both artifacts should stay local to one page
- neither artifact should hide freeform reasoning in long prose

That is enough to make the hard-page loop measurable without making it ornate.
