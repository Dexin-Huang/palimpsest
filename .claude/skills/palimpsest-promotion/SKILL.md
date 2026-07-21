---
name: palimpsest-promotion
description: Qualify, propose, canary, promote, refresh, or roll back a Palimpsest station candidate. Use this skill whenever the user asks whether an experiment can enter production, wants a qualification run, recipe proposal, production canary, candidate promotion, explicit refresh after promotion, rollback, promotion-history review, reproducibility waiver, or recovery from an interrupted recipe commit.
---

# Palimpsest Promotion

Use this skill for the authorization boundary between immutable evaluation
evidence and the selected production recipe. Promotion is not part of ordinary
experiment iteration.

## Read First

- `docs/OPERATIONS.md` sections 5.9, 6, 7, 8, 10, 12, and 13
- `docs/EVALUATION.md` sections 9, 10, 12, 13, and 17
- the canonical indexed report
- the exact baseline and challenger candidate files
- the target recipe source
- any waiver, proposal, canary, promotion, or rollback record involved

Resolve fingerprints from records and source. Never accept identity by filename
or prose alone.

## Choose the Mode

1. **Qualification**: run a frozen challenger against the complete authorizing
   suite.
2. **Proposal**: create an immutable compare-and-swap recipe proposal from a
   qualified report.
3. **Canary and promotion**: execute or verify an exact protected canary, obtain
   explicit human approval, commit the recipe, and append history.
4. **Post-promotion refresh**: explicitly apply the selected station to named
   production manuscripts.
5. **Rollback**: append the exact inverse of one promotion and explicitly
   refresh intended manuscripts.
6. **Commit recovery**: reconcile durable pending intent, recipe source, and
   append-only record after interruption.

Do not combine these into one unreviewed command sequence. Each boundary needs
its own observed result.

## Qualification Preconditions

Verify:

- challenger behavior is frozen;
- suite is explicitly `qualification_eligible` and sufficiently curated;
- baseline matches the current production slot;
- complete suite runs with no `--cases` filter;
- executor is `subprocess`;
- cost ceiling is finite and approved;
- qualification cases were not used to tune the challenger;
- direct metrics, hard limits, protected slices, downstream probes, reliability,
  cost, latency, and judge evidence are complete enough for the suite policy.

Currently checked-in development/conformance suites do not authorize promotion
unless the repository now contains a deliberately reviewed authorizing version.
Never toggle eligibility to bypass a rejected or non-authorizing report.

A qualification command has this shape:

```text
python -m palimpsest bench run --suite SUITE_PATH --baseline BASELINE_PATH --challenger CHALLENGER_PATH --run-id RUN_ID --executor subprocess --workers N --max-cost USD
```

Read the report in table and JSON form. Stop on `rejected`, `inconclusive`,
`failed`, unknown required evidence, mismatched identity, or stale baseline.

## Proposal Preconditions

Before `bench propose`, establish:

```text
terminal report fingerprint verifies
decision is qualified
report suite/baseline/challenger match supplied records
baseline still represents selected recipe slot
required evidence is known
moving/untracked identity has reviewed waiver when required
proposal changes exactly one intended station slot
```

Create retained roots if absent. Immutable writers require existing parents:

```text
mkdir library/evaluations/proposals
mkdir library/evaluations/canaries
mkdir library/evaluations/promotion-history
```

Create but do not apply the proposal:

```text
python -m palimpsest bench propose RUN_ID --recipe RECIPE --recipe-root palimpsest/factory/recipes --baseline BASELINE_PATH --challenger CHALLENGER_PATH --output library/evaluations/proposals/RUN_ID.json
```

Use `--waiver` only for a real, separately reviewed reproducibility exception.
Do not manufacture a waiver for convenience.

## Canary and Promotion

Require:

- exact immutable proposal;
- representative canary document or exact retained canary evidence;
- approved production spend;
- subprocess execution;
- explicit human identity supplied by the user or established policy;
- writable recipe and retained history roots;
- unchanged current recipe hash.

Never invent, infer, or substitute the `--approved-by` identity. An engineering
agent cannot approve its own challenger.

Run a protected canary and commit only after all preconditions:

```text
python -m palimpsest bench promote PROPOSAL_PATH --recipe-root palimpsest/factory/recipes --canary DOC_ID --canary-evidence-output CANARY_PATH --executor subprocess --workers N --approved-by "IDENTITY" --history-root library/evaluations/promotion-history
```

`--canary-evidence` may replace `--canary` only when the retained record verifies
against the exact proposal. Compare-and-swap must reject an intervening recipe
change. Do not merge around it or copy proposed YAML manually.

Inspect the resulting promotion record and recipe source. Preserve pending
intent if interrupted; use the durable commit recovery path rather than rerun
or delete state blindly.

## Post-Promotion Production Refresh

Promotion selects the recipe but does not repeat paid production work. Apply it
to each intended manuscript explicitly:

```text
python -m palimpsest run --doc-id DOC_ID --refresh STATION
python -m palimpsest status --doc-id DOC_ID
python -m palimpsest site
```

Verify the terminal book, EPUB, reader, and evidence links required by the
canary policy.

## Rollback

Resolve the exact original promotion, current candidate, and recorded previous
candidate. Verify the current recipe still matches the promoted state.

```text
python -m palimpsest bench rollback PROMOTION_PATH --recipe-root palimpsest/factory/recipes --current CURRENT_CANDIDATE_PATH --previous PREVIOUS_CANDIDATE_PATH --approved-by "IDENTITY" --history-root library/evaluations/promotion-history --proposal-output ROLLBACK_PROPOSAL_PATH
```

Rollback appends an inverse decision; it never edits or removes the original.
Afterward, explicitly refresh the restored station on intended manuscripts.
Stop on source mismatch instead of forcing a write.

## Forbidden Actions

Do not:

- promote from development-only evidence;
- promote a report whose identities do not match supplied records;
- let judge preference override deterministic hard failure;
- convert missing cost, usage, or evidence to zero;
- rerun paid production implicitly;
- edit the recipe outside the durable commit protocol;
- delete pending intent or append-only history;
- fabricate human approval;
- commit or push unless explicitly requested.

## Output Format

Report each gate separately:

```text
Mode and requested decision:
Report ID + fingerprint + qualification decision:
Suite authorization state:
Baseline/challenger identity match:
Unknown or blocking evidence:
Waiver identity, if any:
Proposal ID + current/proposed recipe hashes:
Canary document + fingerprint + gate results:
Human approval identity:
Promotion/rollback ID:
Recipe commit result:
Production manuscripts explicitly refreshed:
Book/EPUB/site checks:
Next permitted action:
```

A gate not executed is `not executed`, never implied by a later result.
