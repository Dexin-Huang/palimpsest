---
name: palimpsest-experiment
description: Design, initialize, execute, resume, or review a bounded experiment on one Palimpsest pipeline station. Use this skill whenever the user mentions experimenting, testing, benchmarking, comparing a baseline and challenger, trying a new model or prompt, changing parameters or options, evaluating a same-socket variant, initializing an experiment agent, interpreting a bench report, or deciding the next experiment—even if they do not explicitly ask for a Palimpsest skill.
---

# Palimpsest Experiment

Use this skill only in the Palimpsest repository. It governs engineering
experiments; it never authorizes a production recipe change.

## Read First

Read only the relevant sections before acting:

- `docs/OPERATIONS.md` sections 2, 3, 5, 6, 12, and 13
- `docs/EVALUATION.md` sections 2, 3, 8, 12, and 17
- `docs/CONTRACTS.md` for the selected station socket
- the exact baseline candidate, challenger candidate, suite, case, and prompt
  records involved

If the challenger requires station implementation or contract changes, also
read `.claude/skills/palimpsest-station-development/SKILL.md`.

## Classify the Request

Choose one mode:

1. **Initialize an agent**: resolve actual repository paths and produce an
   isolated `claude --agent=engineer --worktree ...` launch plus a complete
   bounded brief.
2. **Design an experiment**: define station, hypothesis, baseline, challenger,
   evidence, hard limits, protected slices, downstream probe, and budget.
3. **Execute development**: fetch, verify, run selected development cases, and
   interpret the report.
4. **Freeze qualification**: stop iteration, preserve candidate identity, and
   prepare a full-suite subprocess command.
5. **Resume incomplete work**: validate immutable run identity before using
   `--resume`.
6. **Review evidence**: read the canonical report and recommend iterate,
   qualify, or stop.

Do not collapse these modes. In particular, development evidence is not a
promotion decision.

## Experiment Contract

Resolve these before a paid call:

```text
station
observable hypothesis
baseline candidate path + fingerprint
challenger candidate path + fingerprint
suite path + fingerprint
development case IDs
hard limits
protected slices
downstream probes
finite cost ceiling
filesystem-safe run ID
allowed files and explicit non-goals
```

Use repo evidence instead of asking the user for paths or identities that can be
resolved locally. Ask only for an actual policy decision such as the maximum
authorized spend.

## Mutation Rules

- Model, prompt, parameters, or options: create a new candidate version.
- Same transformation and artifact socket: create a named station variant and
  candidate; preserve `grain + consumes + optional_consumes + produces`.
- Different inputs, output kind, grain, or artifact shape: stop experiment
  execution and follow the station-development contract first.
- Cases, gold, metrics, slices, thresholds, probes, or qualification policy:
  create a new suite version.
- Never mutate a baseline, used prompt, used suite, gold record, checkpoint, or
  terminal report into a new identity.

Prefer one causal change. When several changes are inseparable, state that the
report supports only their combined effect.

## Agent Initialization

Use one writing agent per worktree. From a reviewed committed `HEAD`:

```text
claude --agent=engineer --worktree STATION-SLUG --name STATION-SLUG
```

Build the prompt from `docs/OPERATIONS.md` section 5.1, replacing every field
with resolved values. The brief must prohibit recipe edits, promotion,
qualification-policy escalation, baseline mutation, unauthorized paid calls,
commit, and push.

An engineering agent authors or diagnoses a challenger. It is not a production
agent cell and never supplies its own human approval.

## Execution Sequence

1. Inspect the logical station and current production candidate.
2. Resolve or create an immutable challenger.
3. Resolve development evidence and declared asset hashes.
4. Fetch source assets when required:

   ```text
   python -m palimpsest bench fetch --suite SUITE_PATH
   ```

5. Verify before execution:

   ```text
   python -m palimpsest bench verify --suite SUITE_PATH
   ```

6. Run selected development cases only within explicit authorization:

   ```text
   python -m palimpsest bench run --suite SUITE_PATH --baseline BASELINE_PATH --challenger CHALLENGER_PATH --run-id RUN_ID --cases CASE_ID --executor inline --workers 1 --max-cost USD
   ```

7. Read both report views:

   ```text
   python -m palimpsest bench report RUN_ID
   python -m palimpsest bench report RUN_ID --format json
   ```

8. Inspect hard limits, every failure, protected slices, unknown evidence,
   downstream probes, cost, and latency before interpreting preference metrics.
9. Recommend exactly one next permitted action: iterate, freeze for
   qualification, or stop.

When paid-call authorization is absent, complete all offline work, provide the
exact run command, and stop before dispatch.

## Qualification Boundary

A qualification run:

- uses the frozen challenger;
- omits `--cases` so every declared case runs;
- uses `--executor subprocess`;
- uses a finite `--max-cost`;
- does not tune against qualification cases;
- may qualify only when the suite itself is authorizing.

All currently checked-in suites are intentionally non-authorizing unless the
repository now proves otherwise. Never flip `qualification_eligible` merely to
make an experiment pass.

## Resume Rules

Resume only an incomplete run with identical suite, candidates, cases, assets,
executor, and budget identity. Omit `--max-cost` to reuse the recorded ceiling
or pass the exact same value. The ceiling cannot be raised on resume. Changed
identity, terminal report, or corrupted checkpoint requires preservation and a
new run ID—not deletion.

## Forbidden Actions

Do not:

- edit a production recipe;
- run `bench propose`, `bench promote`, or `bench rollback`;
- run a production manuscript with `--refresh`;
- fabricate gold, cost, usage, judge evidence, or approval;
- convert unknown evidence to zero;
- reuse a completed run ID;
- commit or push unless explicitly requested.

## Handoff Format

Report:

```text
Mode:
Station and hypothesis:
Files changed:
Baseline path + fingerprint:
Challenger path + fingerprint:
Suite path + fingerprint:
Cases and slices:
Verification performed:
Run ID + report fingerprint:
Decision and reasons:
Failed cases and unknown evidence:
Observed candidate/judge/total cost:
Production state unchanged:
Next permitted action:
```

Ground every value in a file, command, or report. Mark anything not observed as
an inference.
