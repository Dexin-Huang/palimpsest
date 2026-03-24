# Autoresearch Reconstruction Loop

This document describes how to adapt the `autoresearch` pattern to
Palimpsest's page-reconstruction problem.

## Core Idea

Use the same role split as the local grunt-worker system:

- human defines the objective and benchmark
- mutation worker edits prompts / reconstruction policy
- runner executes page reconstructions
- evaluator computes a frozen score
- controller accepts a change only when the score improves

The agent should not be allowed to edit the benchmark or the evaluator during
the loop.

## Working Analogy

`autoresearch` has:
- fixed training script
- fixed budget per run
- fixed metric
- agent mutates code

Palimpsest should have:
- fixed reconstruction policy surface
- fixed benchmark page set
- fixed score function
- agent mutates prompts / merge policy / routing policy

## What The Agent Can Change

Good mutation targets:
- page scaffold settings
- band count / overlap policy
- prompt wording
- model routing rules
- merge heuristics
- smoothing heuristics

Bad mutation targets:
- gold transcriptions
- held-out benchmark pages
- scoring script
- any manual labels used for evaluation

## Two Scores

There should be two different scores:

### 1. Gold Score

Use this for real benchmark progress.

Requires:
- a small frozen benchmark set
- gold diplomatic text
- stable per-page metadata

Recommended composition:

`page_score = 100 * (`
- `0.45 * char_similarity`
- `0.20 * line_f1`
- `0.10 * reading_order_score`
- `0.10 * term_consistency`
- `0.10 * overlap_cleanliness`
- `0.05 * provenance_coverage`
`) - penalties`

Recommended penalties:
- duplicate overlap lines
- obvious hallucinated lines
- broken section numbering
- missing boundary content

### 2. Proxy Score

Use this online when gold is unavailable.

Recommended composition:

`proxy_score = 100 * (`
- `0.35 * overlap_agreement`
- `0.25 * rerun_consistency`
- `0.20 * term_spelling_consistency`
- `0.10 * duplicate_cleanliness`
- `0.10 * output_validity`
`) - penalties`

This score is useful for local search, but it should not be confused with real
quality.

## Benchmark Shape

Start with a tiny frozen set:

- 4 easy pages
- 4 dense bilingual pages
- 4 hard pages with overlap / boundary issues

Each page should include:
- source image
- optional crop fixtures
- gold line list
- important term list
- page difficulty weight

Weight hard pages more heavily than easy pages.

## Acceptance Rule

The controller should accept a mutation only if:

1. aggregate gold score improves by `epsilon`, or
2. gold score is tied and a secondary metric improves

Secondary metrics:
- lower duplicate penalty
- lower hallucination penalty
- fewer unresolved boundary flags

Do not accept a change just because one page got better.

## Worker Roles

### Mutation Worker

Allowed to edit:
- prompts
- policy code
- merge / smooth heuristics

Not allowed to edit:
- benchmark labels
- score code

### Runner Worker

Runs the benchmark pages and stores artifacts.

### Evaluator Worker

Read-only.

Computes:
- per-page score
- aggregate score
- delta from previous best

### Controller

Keeps:
- best score
- accepted commits
- rejected commits
- run history

## What To Optimize First

Do not optimize for speed first.

Optimize for:
- stable page completion
- low duplicate boundary artifacts
- term fidelity
- no hallucination

Only after that should time / cost become part of the objective.

## First Practical Version

The first loop should be small:

- benchmark only `f200r` plus a few companion pages
- mutate only page-workspace policy and smoothing policy
- score mostly on line similarity + duplicate penalties
- keep the evaluator script frozen

That is enough to make the `autoresearch` pattern real inside Palimpsest.
