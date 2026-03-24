# Reconstruction System

Purpose: define the simplest viable architecture for page reconstruction in
Palimpsest.

This is the hard-page lane.

It should do more with less:
- few primitives
- few roles
- few scores
- no freeform agent chaos

Concrete JSON/file contracts live in:
- `docs/RECONSTRUCTION_CONTRACTS.md`

## 1. Design Rule

Palimpsest should not have a giant "AI system."

It should have a small reconstruction machine built from five objects:

1. `page.route`
2. `page.workspace`
3. `page.issue`
4. `page.action`
5. `page.score`

Everything else is derived.

---

## 2. Golden Path

`image -> route ->`
- `easy`: direct transcription -> `canonical.page`
- `hard`: workspace loop -> `canonical.page`

Then:

`canonical.page -> diplomatic.page -> readable edition`

The hard-page system exists only to improve the `canonical.page` boundary.

---

## 3. Core Objects

### `page.route`

Purpose:
- decide whether a page is easy, medium, hard, or blocked

Inputs:
- cheap CV features
- optional tiny classifier
- optional scout transcription health

Fields:
- `page_id`
- `difficulty`: `easy | medium | hard | blocked`
- `reasons`
- `cv_score`
- `scout_score`
- `recommended_path`

Rule:
- routing should be cheap and mostly deterministic

### `page.workspace`

Purpose:
- persistent state for hard-page reconstruction

Contains:
- source image
- crop scaffold
- crop outputs
- merged output
- smoothed output
- unresolved issues
- score history

Rule:
- the workspace persists
- the agent does not reset it unless explicitly told to

### `page.issue`

Purpose:
- represent one local problem

Examples:
- overlap duplication
- weak band
- inconsistent proper name
- broken section boundary
- invalid JSON on one crop

Fields:
- `issue_id`
- `type`
- `severity`
- `status`: `open | accepted | rejected | stale`
- `evidence`
- `local_score`

Rule:
- the agent should work on issues, not on the whole page at once

### `page.action`

Purpose:
- represent one bounded move inside the workspace

Examples:
- `transcribe_crop`
- `rerun_crop`
- `split_crop`
- `merge_boundary`
- `smooth_boundary`
- `mark_uncertain`
- `accept_candidate`

Fields:
- `action_id`
- `type`
- `target`
- `inputs`
- `outputs`
- `cost`
- `score_delta`

Rule:
- every action should be reviewable
- every action should be scoreable

### `page.score`

Purpose:
- decide whether the page is getting better

Fields:
- `global_score`
- `component_scores`
- `penalties`
- `issue_scores`
- `judge_decisions`

Rule:
- the score must be more stable than the agent

---

## 4. Routing

The routing system should stay simple.

### Stage 1: Cheap CV Pass

Run on every page.

Signals:
- blankness
- footer / watermark ratio
- skew
- contrast
- bleed-through estimate
- connected-component density
- writing-area size
- estimated line density
- likely column count

This should be local and cheap.

### Stage 2: Cheap Scout Pass

Run only on nontrivial pages.

Use:
- one `flash-lite` scout pass

Signals:
- valid JSON or not
- truncation or retry
- text density
- script mix
- obvious garbling

### Stage 3: Difficulty Decision

Routing rule:
- `easy`
  one clean scout pass, simple layout, low disagreement
- `medium`
  deterministic crop split + merge
- `hard`
  workspace loop with issue tracking
- `blocked`
  send to human review or benchmark-only lane

The router does not need to be clever.

It only needs to avoid sending every page into the expensive path.

---

## 5. Worker Model

Reuse the grunt-worker principle:
- narrow workers
- hard constraints
- one job per worker

### `route-worker`

Job:
- compute `page.route`

Allowed:
- CV features
- scout pass

Not allowed:
- freeform editing of the page

### `reconstruct-worker`

Job:
- work one `page.issue`

Allowed:
- call page actions
- update workspace

Not allowed:
- redefine the score
- rewrite the whole page from scratch

### `grade-worker`

Job:
- adjudicate disputed cases only

Allowed:
- inspect evidence packet
- return `exact | variant | different | uncertain`

Not allowed:
- invent text
- rewrite the page

### `controller`

Job:
- choose next issue
- accept or reject action results
- maintain score history

Rule:
- controller is deterministic where possible

---

## 6. Score

There are only two scores that matter:

### `machine_score`

Fast and deterministic.

Use for every page.

Components:
- `output_validity`
- `duplicate_cleanliness`
- `overlap_agreement`
- `rerun_consistency`
- `term_spelling_consistency`
- `boundary_completeness`

### `gold_score`

Use only on the benchmark set.

Components:
- `char_similarity`
- `line_f1`
- `reading_order_score`
- `term_consistency`
- `overlap_cleanliness`
- `provenance_coverage`

### Suggested Formula

For benchmark pages:

`page_score = 100 * (`
- `0.45 * char_similarity`
- `0.20 * line_f1`
- `0.10 * reading_order_score`
- `0.10 * term_consistency`
- `0.10 * overlap_cleanliness`
- `0.05 * provenance_coverage`
`) - penalties`

Penalties:
- duplicate lines
- unsupported corrections
- hallucinated lines
- broken numbering
- unresolved hard boundaries

---

## 7. Reasoning Judge

The reasoning judge should be small and constrained.

Use it only when deterministic metrics cannot decide.

Example:
- `chai` vs `chung`
- `Tchu hi` vs `Chu hi`
- same line or actually different line

The judge should receive:
- crop image or evidence packet
- candidate A
- candidate B
- similarity stats
- nearby context

And return:
- `exact`
- `variant`
- `different`
- `uncertain`

Important rule:
- if similarity is high and the judge says `different`, it must provide proof

Without proof:
- fall back to `variant` or `uncertain`

This prevents unsupported â€œsmartâ€ corrections.

---

## 8. Persistent Improvement Loop

Unlike classic `autoresearch`, the reconstruction loop should not reset the
page every run.

It should be persistent:

1. load workspace
2. find weakest issue
3. perform one bounded action
4. rescore page
5. keep only improvements
6. continue until convergence or budget stop

This is closer to restoration than to benchmark reruns.

The page should get better locally over time.

---

## 9. Minimal File Layout

For one page:

```text
experiments/<page>_workspace/
  workspace.json
  crops/
  transcriptions/
  merged/
  smoothed/
  scores/
  issues.json
  history.jsonl
```

Keep the state local to the page.

Do not hide reconstruction state inside generic run logs.

---

## 10. First Version

The first real system should stay small.

Build only:

1. router
2. workspace
3. merge / smooth actions
4. machine score
5. constrained judge for disputes

Do not build yet:
- broad multi-agent swarms
- cross-page optimization
- complex planner hierarchies
- dynamic evaluator mutation

The first job is:
- make hard pages improve reliably with a persistent local loop

That is enough.
