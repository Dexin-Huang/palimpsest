# Repository Operations Guide

Status: **canonical operator runbook** (2026-07-20).

This guide defines how work moves through Palimpsest: running manuscripts,
creating experiments, changing stations, governing benchmark evidence,
promoting a winner, rolling it back, and releasing repository changes. It is
the practical companion to the normative architecture in
[`FACTORY.md`](FACTORY.md), the live generated contract graph in
[`CONTRACTS.md`](CONTRACTS.md), and the evaluation design in
[`EVALUATION.md`](EVALUATION.md).

The core rule is separation of concerns:

```text
production operation  -> work order -> recipe -> artifacts -> book
component experiment  -> candidate + suite -> paired run -> report
production change      -> qualified report -> proposal -> canary -> promotion
recovery               -> resume an incomplete run OR append an exact rollback
```

A benchmark run never changes production. A recipe edit is not evidence. A
passing local experiment is not permission to skip the canary. Paid production
work never reruns merely because configuration changed.

## 1. Sources of truth

Use the narrowest authority for the question being answered.

| Question | Source of truth |
|---|---|
| What artifact kinds and station sockets exist? | `palimpsest/factory/core/contracts.py`, station registry, generated `CONTRACTS.md` |
| How does the production line execute? | `FACTORY.md` and `palimpsest/factory/core/` |
| Which stations and configurations does a manuscript use? | `palimpsest/factory/recipes/*.yaml` |
| What behavior is one experiment comparing? | `palimpsest/factory/candidates/<station>/*.yaml` |
| What evidence can decide that experiment? | `palimpsest/factory/evaluation/suites/<station>/*.yaml` and its cases/gold |
| What actually happened in a run? | Fingerprinted report under `library/evaluations/runs/<run_id>/report.json` and its SQLite index |
| Why did production change? | Proposal, canary evidence, append-only promotion record, and recipe diff |
| How should evaluation and promotion behave? | `EVALUATION.md` |
| How should an operator perform the work? | This guide |

`CONTRACTS.md` is generated. Never edit it by hand. Run:

```text
python -m palimpsest graph --write-docs
```

after changing an artifact contract or station socket.

## 2. Classify the work before editing

Every change belongs to one primary lane. Do not smuggle one lane through
another.

| Intended change | Correct lane |
|---|---|
| Run or resume a manuscript with the selected recipe | Production operation |
| Change model, prompt, parameters, or station options | New candidate experiment |
| Change an algorithm without changing its artifact socket | New station variant and candidate experiment |
| Change required inputs, output kind, grain, or artifact shape | Contract/station change before any experiment |
| Change benchmark cases, gold, metrics, slices, thresholds, or policy | New suite version |
| Install a proven candidate in a recipe | Proposal, canary, promotion |
| Restore the previous selected candidate | Append-only rollback |
| Correct an urgent production defect | Incident protocol; preserve evidence, repair, verify, canary, explicit refresh |

One experiment evaluates one logical station slot. If a proposed improvement
requires coordinated changes to several stations, split it into independently
measurable station changes and promote them sequentially. The current promotion
contract does not authorize an opaque multi-station bundle.

## 3. Environment preflight

Create and activate the repository-local environment as documented in the
README, then establish a clean baseline:

```text
python -m pip check
python -m palimpsest graph
python -m palimpsest bench list
```

Before a network or model-backed command:

1. Confirm `.env` contains the intended credentials and model overrides.
2. Confirm the candidate uses a fixed model identity if automatic
   qualification is expected. Moving aliases require an explicit
   reproducibility waiver.
3. Identify which command can incur cost.
4. Set a finite `--max-cost` on every model- or judge-backed benchmark run.
5. Use `--executor subprocess` for qualification and canaries.
6. Keep the production library and evaluation run roots separate.

The default separation is already correct:

```text
production ledger       library/factory.db
evaluation index        library/evaluations/evaluation.sqlite3
evaluation run evidence library/evaluations/runs/
evaluation objects      library/evaluations/objects/
production workspaces   library/<doc_id>/
```

The ledger and derived evidence are ignored by Git. Back up
`library/factory.db` and `library/evaluations/` together when their history must
survive the workstation. Never hand-edit either the SQLite index or a
fingerprinted report.

## 4. Protocol: run a manuscript

### 4.1 Create or adopt the work order

For a new IIIF source:

```text
python -m palimpsest intake --doc-id DOC_ID --manifest MANIFEST_URL --recipe RECIPE
```

For a workspace that already contains canonical `metadata.json` and
`page_list.json` source records:

```text
python -m palimpsest adopt --doc-id DOC_ID --recipe RECIPE
```

Intake and adoption establish source identity. Do not modify generated or paid
artifacts to simulate a different input. If the canonical source records are
wrong, correct the source boundary explicitly and let freshness propagate.

### 4.2 Execute or resume

```text
python -m palimpsest run --doc-id DOC_ID --workers 6 --model-workers 3
```

The conductor uses a barrier between page stations. `--workers` controls
non-model page cells; `--model-workers` controls model-backed page cells and
defaults to `min(3, --workers)`. Dual-reader candidates start concurrently,
while `PALIMPSEST_MODEL_PROVIDER_WORKERS` limits simultaneous calls to each
provider independently (default `3`). Increasing page workers beyond a
provider's useful concurrency usually increases latency and timeout risk rather
than throughput.

The conductor decides by fingerprint:

| Cell state | Action |
|---|---|
| Missing | Run |
| Fresh | Skip |
| Input-stale | Run |
| Configuration-outdated | Report; do not repeat paid work |
| Failed | Preserve the failure; a later run may retry according to the work order |

Use `--executor subprocess` when isolation matters. Inline and subprocess
execution must produce the same artifact contract.

Hard deadlines are positive integer seconds configured in `.env`:

| Setting | Default | Boundary |
|---|---:|---|
| `PALIMPSEST_MODEL_TIMEOUT_SECONDS` | 7200 | One provider call |
| `PALIMPSEST_AGENT_TIMEOUT_SECONDS` | 14400 | One agent run or repair turn |
| `PALIMPSEST_CELL_TIMEOUT_SECONDS` | 28800 | One isolated subprocess cell |

The outer cell deadline intentionally exceeds model and agent deadlines.
Provider-call deadline expiry is terminal: the gateway does not automatically
repeat a potentially completed paid call. Raise a deadline for known long
folios rather than increasing concurrency.

### 4.3 Apply an intentional configuration change

A recipe, prompt, model, option, or implementation change makes affected cells
outdated. Rerun the changed station only after the change is authorized:

```text
python -m palimpsest run --doc-id DOC_ID --refresh STATION
```

The downstream stale subgraph then follows from artifact fingerprints. Do not
delete provenance or outputs to force a rerun; `--refresh` is the auditable
control.

### 4.4 Inspect terminal output

```text
python -m palimpsest status --doc-id DOC_ID
python -m palimpsest site
```

A complete manuscript produces:

```text
library/<doc_id>/book/book.json
library/<doc_id>/book/<doc_id>.epub
site/index.html
```

A production operation is complete only when the status is understood and the
book, EPUB, and reader output required for that operation have been checked.

## 5. Protocol: start a new experiment

An experiment begins with an explicit replacement question, not with an
untracked prompt edit.

### 5.1 Initialize an isolated engineering agent

An engineering agent changes repository code or experiment records and runs
the benchmark as an operator. It is not a production agent cell. Production
agent cells are airlocked station executors described in
[`AGENT_WORKERS.md`](AGENT_WORKERS.md); they do not receive the repository or
design their own experiments.

Use one branch and one working tree per experiment. Never run two writing
agents in the same working tree. Start from a reviewed committed `HEAD`;
uncommitted changes in the primary checkout are not part of the agent's base.

Claude Code can create the isolated worktree and start the repository's full
engineering agent in one command:

```text
claude --agent=engineer --worktree STATION-SLUG --name STATION-SLUG
```

When the session opens, ask it to use the `palimpsest-experiment` project skill
and provide the bounded facts below. Use `--bg` plus that complete prompt only
when the experiment should run as a managed background agent.

If an exact non-`HEAD` base commit or a manually located worktree is required,
create it first, enter it, and then run `claude --agent=engineer`:

```text
git worktree add ..\palimpsest-exp-STATION-SLUG -b experiment/STATION-SLUG BASE_COMMIT
cd ..\palimpsest-exp-STATION-SLUG
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade --editable ".[dev]"
claude --agent=engineer
```

On macOS or Linux, use a forward-slash worktree path and activate with
`source .venv/bin/activate`. The automatically created worktree may also need
its own repository-local virtual environment. Supply credentials through the
normal environment or a local ignored `.env`; never paste secrets into the
agent brief.

Use `claude --agent=quick` only for a mechanical record correction that does
not design, execute, or interpret an experiment.

The source-controlled
`.claude/skills/palimpsest-experiment/SKILL.md` owns the reusable experiment
procedure, safety boundaries, brief template, and handoff format. Invoke it
explicitly so the engineering session does not reconstruct the workflow from
generic agent behavior:

```text
Use palimpsest-experiment for one bounded station experiment.

Station: STATION
Hypothesis: OBSERVABLE FAILURE AND EXPECTED IMPROVEMENT
Baseline candidate: BASELINE_PATH
Challenger type: MODEL/PROMPT/PARAMETERS/OPTIONS/SAME-SOCKET VARIANT
Suite: SUITE_PATH_OR_SCOPE
Development cases: CASE_IDS
Protected slices: SLICE_NAMES
Downstream behavior that must not regress: PROBE
Paid-call authorization: NONE OR MAXIMUM USD
Allowed changes: EXACT FILES OR DIRECTORIES
Non-goals: NO UNRELATED CLEANUP; NO OTHER STATIONS
```

Resolve every uppercase field before launch or explicitly assign only the
bounded discovery that remains. The skill requires a distinct immutable
challenger, verifies records before paid work, preserves production recipes and
prior evidence, uses only development cases while iterating, and returns
fingerprinted evidence plus the next permitted decision. It stops before a paid
call when authorization is `NONE`.

The experiment agent may author and diagnose a challenger, but it never
approves its own production change. Qualification, canary review, and the human
identity passed to `--approved-by` remain separate gates.

When the agent finishes, review its diff and immutable report before merging
the branch. Keep the worktree until the report paths, run ID, and any local
evidence needed for handoff have been retained or backed up.

### 5.2 Write the experiment contract

Record these facts in the new candidate and suite records:

1. **Station:** the one logical production slot under test.
2. **Hypothesis:** the observable failure being reduced.
3. **Baseline:** the exact candidate matching the currently selected recipe
   behavior.
4. **Challenger:** the complete replacement configuration.
5. **Primary metrics:** direct measures of the station mission.
6. **Hard limits:** failures that no average improvement can offset.
7. **Protected slices:** known archive or content regimes that cannot regress.
8. **Downstream probe:** the next meaningful behavior that must remain intact.
9. **Operational limits:** cost, latency, and failure ceilings.
10. **Decision policy:** development-only evidence or an authorizing
    qualification suite.

Prefer a challenger that isolates one causal idea. Several parameter changes
may form one candidate when they are inseparable, but the report can then
support only the combined configuration, not claims about each individual
change.

### 5.3 Choose the mutation type

#### Model, prompt, parameters, or options

Create a new candidate file:

```text
palimpsest/factory/candidates/<station>/<descriptive-version>.yaml
```

If prompt content changes, create a new prompt resource or deliberately version
the existing prompt name before collecting evidence. Candidate identity
includes the resolved prompt hash, but retaining the old content is necessary
for practical reproduction.

Never edit the baseline candidate into the challenger. Both definitions must
remain resolvable for the paired run and later audit.

#### Algorithm with the same socket

Implement and register a new named `Station.variant`. It must retain the same:

```text
grain + consumes + optional_consumes + produces
```

Declare every behavior-bearing package source in `production_dependencies`.
Then create a candidate that selects the new variant. Do not create a new
logical station merely to compare a second implementation of the same
transformation.

#### New artifact shape or transformation

This is architecture work, not yet a candidate experiment:

1. define or change the artifact contract;
2. implement the logical station and workspace path;
3. compose it into a recipe;
4. regenerate `CONTRACTS.md`;
5. add contract and conductor tests;
6. add a candidate and an evaluation suite;
7. only then begin comparative evidence collection.

One implementation does not justify an interface. A new logical station must
own a distinct transformation and output concept.

#### Export or import an agent rig

A `.palrig` archive contains one complete model-backed candidate identity. It
includes these components:

- the fixed model name;
- the canonical candidate record;
- the exact skill prompt;
- the station implementation source closure;
- the Python, package, and applicable OMP runtime versions.

Export a rig only after the candidate resolves:

```text
python -m palimpsest rig export \
  --candidate CANDIDATE_PATH \
  --output CANDIDATE.palrig
```

Copy the archive SHA-256 from the export output through an authenticated
channel. Then import the rig on a compatible Palimpsest installation:

```text
python -m palimpsest rig import CANDIDATE.palrig \
  --expected-sha256 ARCHIVE_SHA256
```

Import authenticates the archive against the supplied SHA-256 value. This check
proves origin only when the value came through a trusted channel. Import also
verifies the installed runtime. It stores the archive at
`library/rigs/<rig_fingerprint>/rig.palrig`.

Import does not execute the rig or install the bundled implementation
snapshots. A later benchmark executes extensions declared in `candidate.yaml`.
Review and trust the rig before you run that benchmark.

Use the stored `.palrig` path in a development benchmark where a candidate path
is accepted. The runner marks the imported candidate as untracked. To make it
eligible for qualification, create and review an immutable candidate under
`palimpsest/factory/candidates/`, then collect new evidence with that tracked
identity.

The archive does not contain credentials, remote model weights, benchmark
cases, gold, or reports. Transfer those records through their existing
content-addressed paths.


### 5.4 Create or select evidence

Tracked evidence lives under:

```text
palimpsest/factory/evaluation/suites/<station>/
palimpsest/factory/evaluation/cases/<station>/
palimpsest/factory/evaluation/gold/<station>/
```

Rules:

- candidate inputs and scorer-only gold remain separate;
- every local or remote asset has a SHA-256 digest;
- cases identify meaningful strata and protected slices;
- candidate execution never receives hidden gold paths or expected answers;
- development cases may guide iteration;
- qualification cases must not be used to tune the challenger;
- changing cases, gold, metrics, thresholds, slices, probes, or policy creates a
  new suite version and fingerprint;
- retain prior suite files once reports depend on them.

All currently checked-in suites are development or conformance evidence and
have `qualification_eligible: false`. They can reject a candidate and exercise
the complete runner, but they cannot authorize promotion. To create an
authorizing suite, curate sufficient evidence, copy it to a new version, set
its qualification policy deliberately, and review that change as an evidence
change rather than a formatting edit.

### 5.5 Fetch declared external assets

Fetching is content-addressed and hash-verified:

```text
python -m palimpsest bench fetch --suite SUITE_PATH
```

A changed URL cannot silently replace content because the declared SHA-256 must
still match. Fetch does not execute candidates or judges.

### 5.6 Verify before execution

```text
python -m palimpsest bench verify --suite SUITE_PATH
```

Verification resolves tracked candidates, suites, judges, prompts, station
variants, artifact sockets, parameters, local assets, fetched source objects,
and indexed reports without executing a candidate. It must pass before a paid
call.

If verification finds a malformed or ambiguous identity, fix the record. Do
not weaken validation or add a fallback alias.

### 5.7 Run a development subset

Use a new filesystem-safe run ID. IDs may contain letters, numbers, periods,
underscores, and hyphens; avoid dates without a descriptive experiment name.
For example:

```text
read-f004r-high-thinking-v1-dev
```

A development invocation is:

```text
python -m palimpsest bench run --suite SUITE_PATH --baseline BASELINE_PATH --challenger CHALLENGER_PATH --run-id RUN_ID --cases CASE_ID --executor inline --workers 1 --max-cost USD
```

Use `--cases` only for development diagnosis. Inline execution is acceptable
for local debugging; it is not the qualification protocol.

Inspect the immutable terminal report:

```text
python -m palimpsest bench report RUN_ID
python -m palimpsest bench report RUN_ID --format json
```

Read per-case failures, protected slices, unknown evidence, downstream probes,
cost, and latency—not only the top-line decision.

### 5.8 Iterate without erasing identity

When candidate behavior changes:

1. create a new candidate version or behavior-bearing resource;
2. retain the prior candidate used by recorded runs;
3. verify again;
4. use a new run ID;
5. compare reports on the same frozen development cases.

Do not reuse a completed run ID. Do not mutate checkpoints. Do not relabel a
failed run as a different experiment.

### 5.9 Run qualification

Qualification uses the complete declared suite, process isolation, and a finite
budget:

```text
python -m palimpsest bench run --suite SUITE_PATH --baseline BASELINE_PATH --challenger CHALLENGER_PATH --run-id RUN_ID --executor subprocess --workers N --max-cost USD
```

Omit `--cases`; qualification is not allowed to choose only favorable examples.
The runner compares identical inputs, runs declared judges and probes, applies
hard limits before preference metrics, and derives the decision from the
fingerprinted suite policy.

Possible outcomes:

| Outcome | Required action |
|---|---|
| `rejected` | Keep production unchanged; investigate the recorded reasons |
| `inconclusive` | Keep production unchanged; collect better evidence or prefer the baseline |
| `failed` | Preserve the terminal report; correct the cause and start a new run |
| `qualified` | Review the full report, then optionally create a proposal |

A newer model label, better judge prose, or higher unpaired average is not a
qualification decision.

## 6. Protocol: resume or recover an evaluation

Use resume only for an incomplete run whose immutable identities are unchanged:

```text
python -m palimpsest bench run --suite SUITE_PATH --baseline BASELINE_PATH --challenger CHALLENGER_PATH --resume RUN_ID --executor subprocess --workers N --max-cost USD
```

Resume validates the suite, baseline, challenger, cases, asset hashes, executor,
and budget identity against the run manifest. Completed pair and judge
checkpoints are content-verified and reused; incomplete work is dispatched
without duplicating completed observations or known cost.

Choose the recovery action by condition:

| Condition | Action |
|---|---|
| Process stopped but identities and inputs are unchanged | `--resume RUN_ID` |
| Cost ceiling stopped dispatch | The ceiling is part of run identity and cannot be raised on resume; start a reviewed new run with a new ID |
| Candidate, suite, prompt, case, asset, executor, or manifest changed | Start a new run ID |
| Run already has a terminal report | Read it; never resume or overwrite it |
| Checkpoint or report fingerprint fails | Preserve the directory and investigate corruption; do not delete evidence to make resume pass |
| Provider returned unknown usage or cost | Preserve unknown; it may block qualification |

Failed attempts remain part of reliability and cost evidence. Recovery must not
turn a real failed call into an apparently clean sample.

## 7. Protocol: propose and promote

Qualification and production activation are separate human decisions.

### 7.1 Review prerequisites

Before proposing:

- report status is terminal and its fingerprint verifies;
- decision is `qualified`;
- report suite, baseline, and challenger identities match the intended files;
- the baseline still represents the selected recipe slot;
- all required evidence is known;
- moving or untracked identities have an explicit reviewed waiver;
- catastrophic cases have been inspected individually;
- the proposed recipe changes only the intended station slot.

### 7.2 Create an immutable proposal

Create the retained artifact roots once before the first proposal. Immutable
record writers require the parent directories to exist and never invent an
operator-selected retention path:

```text
mkdir library/evaluations/proposals
mkdir library/evaluations/canaries
mkdir library/evaluations/promotion-history
```

```text
python -m palimpsest bench propose RUN_ID --recipe RECIPE --recipe-root palimpsest/factory/recipes --baseline BASELINE_PATH --challenger CHALLENGER_PATH --output library/evaluations/proposals/RUN_ID.json
```

Add `--waiver WAIVER_PATH` only for a reviewed reproducibility exception. A
proposal captures the current recipe hash and proposed recipe hash; it does not
modify production.

### 7.3 Run the protected canary and commit

Use a representative manuscript already available under the production library
root:

```text
python -m palimpsest bench promote library/evaluations/proposals/RUN_ID.json --recipe-root palimpsest/factory/recipes --canary DOC_ID --canary-evidence-output library/evaluations/canaries/RUN_ID.json --executor subprocess --workers N --approved-by "IDENTITY" --history-root library/evaluations/promotion-history
```

The canary runs in isolation using the proposed recipe and checks the suite's
required production outcomes. Promotion occurs only after the canary passes and
an explicit human identity approves it. The recipe commit uses compare-and-swap
against the source hash captured by the proposal, so a stale proposal cannot
overwrite an intervening recipe change.

Promotion and rollback records index into the evaluation index
(`library/evaluations/evaluation.sqlite3`, the same DB `bench list` reads);
they are evaluation evidence and travel with the run reports. The protected
canary reads the production ledger separately for its work-order check, via
`--ledger-db` (default `library/factory.db`) — the two databases stay split.

If an independently retained canary record already exists, `--canary-evidence`
may replace `--canary` only when the command verifies that evidence against the
exact proposal.

Promotion is append-only and crash-recoverable. Do not manually copy the
proposed YAML into the recipe or manually manufacture a promotion record.

### 7.4 Apply the selected candidate to production work

Promotion changes the selected recipe. It does not silently repeat paid cells.
For each manuscript intentionally receiving the new candidate:

```text
python -m palimpsest run --doc-id DOC_ID --refresh STATION
python -m palimpsest status --doc-id DOC_ID
python -m palimpsest site
```

Review the resulting book and evidence path. The benchmark establishes bounded
fitness; the production artifact remains the final product check.

## 8. Protocol: rollback

Rollback restores the exact prior candidate named by a promotion and appends an
inverse decision. It does not rewrite history.

Inputs required:

- the promotion record produced by the successful promotion;
- the exact currently selected candidate file;
- the exact previous candidate file;
- an approving identity;
- the same recipe and history roots.

Command:

```text
python -m palimpsest bench rollback PROMOTION_PATH --recipe-root palimpsest/factory/recipes --current CURRENT_CANDIDATE_PATH --previous PREVIOUS_CANDIDATE_PATH --approved-by "IDENTITY" --history-root library/evaluations/promotion-history --proposal-output library/evaluations/proposals/ROLLBACK_ID.json
```

The rollback path uses the same recipe compare-and-swap and durable commit
protocol as promotion. If the current recipe no longer matches the promotion,
stop and reconcile the intervening decision rather than forcing the write.

After rollback, production work is still protected from implicit paid reruns:

```text
python -m palimpsest run --doc-id DOC_ID --refresh STATION
```

Retain both the original promotion and inverse rollback records.

## 9. Protocol: change a station or contract

### 9.1 Same logical station, same socket

Use a new variant when the implementation changes but the transformation
contract does not. Required checks:

1. variant registers under the existing logical station;
2. socket exactly matches every sibling variant;
3. behavior-bearing package sources are declared in
   `production_dependencies`;
4. candidate resolution yields a distinct implementation fingerprint;
5. existing variants still resolve;
6. a station-specific suite can compare the variants on identical cases;
7. production remains on the current variant until promotion.

### 9.2 New logical station or artifact contract

Required sequence:

1. define the artifact kind once in `core/contracts.py`;
2. define its one workspace path through `workspace/layout.py`;
3. implement one station that produces it;
4. register the station and compose it in a recipe;
5. add focused contract, station, conductor, and failure-path tests;
6. regenerate `docs/CONTRACTS.md`;
7. create at least one tracked candidate;
8. define its station fitness metrics and suite;
9. exercise an end-to-end manuscript path before treating the station as
   production-ready.

Do not add aliases, compatibility shims, duplicate paths, or a second
conductor. Migrate callers cleanly when a contract changes.

## 10. Protocol: govern benchmark evidence

Evidence changes are product decisions because they change what the factory is
allowed to optimize.

### Case additions

- use source-grounded, hash-pinned inputs;
- add a stable case ID and relevant strata;
- keep expected answers out of candidate-visible inputs;
- document adjudication method and version;
- include known hard negatives and archive-specific failure modes;
- run baseline calibration before setting thresholds.

### Gold corrections

Never silently replace gold used by an existing suite. Create a new gold
version, update a new case or suite version, and explain the adjudication basis
in the record. A corrected answer changes evidence identity even when candidate
code is unchanged.

### Metric or threshold changes

A metric must measure the named station mission or demonstrated downstream
value. Hard correctness, provenance, and catastrophic failures stay separate
from average quality. Any change to metric direction, threshold, confidence,
slice policy, probe, or operational budget requires a new suite fingerprint and
fresh qualification evidence.

### Promotion eligibility

`qualification_eligible: true` is an authorization boundary, not a maturity
label. Set it only when:

- the corpus is independently curated rather than prompt-tuned;
- minimum case and protected-slice counts are defensible;
- gold and judge behavior have been calibrated;
- operational limits reflect real production constraints;
- required downstream and canary gates are present;
- a human owner accepts responsibility for the policy.

## 11. Protocol: urgent defect or interrupted production

For an operational incident:

1. stop dispatch if continued work could corrupt evidence or incur waste;
2. preserve the failing artifact, provenance, ledger row, stderr, and recipe;
3. classify the fault as source, contract, station, provider, executor, or
   presentation behavior;
4. fix the cause rather than deleting the symptom;
5. run the smallest deterministic reproduction and the affected focused tests;
6. run the station's conformance or development suite;
7. run an isolated canary for behavior-bearing production changes;
8. apply an explicit `--refresh STATION` only to intended manuscripts;
9. inspect status and terminal book output;
10. record an exact rollback if a prior promotion is being reversed.

Never edit an artifact's provenance to make it look fresh. Never mark a failed
ledger row complete by hand. Never bypass recipe compare-and-swap because a
change is urgent; the durable protocol is specifically what makes interrupted
commits recoverable.

## 12. Protocol: verify and release repository changes

### Publish the reader-independent library release

The local AWS profile carries object-store authority; credentials never enter
the bundle or repository. Publish the current validated books to their
content-addressed R2 prefix:

```text
python -m palimpsest publish \
  --bucket alexandria \
  --profile alexandria-r2 \
  --endpoint-url https://13a51693c42fab5925c5ae7d506c06e1.r2.cloudflarestorage.com \
  --public-base-url https://releases.slothful.ai
```

The command atomically rebuilds the local bundle, uploads it beneath
`releases/<bundle_id>/`, and fails unless the remote object names and sizes
exactly match the bundle. The downstream Alexandria importer verifies every
declared SHA-256 digest from the printed public URL. Do not overwrite a release
under another bundle ID or treat local `publication/` output as durable storage.


Verification is proportional to the changed boundary.

| Change | Minimum focused proof |
|---|---|
| Candidate, judge, suite, case, gold, or prompt | `bench verify` plus the affected evaluation tests |
| Station implementation or variant | affected station tests plus its evaluation suite/test module |
| Artifact contract, socket, recipe, or workspace path | graph regeneration, contract test, conductor tests, affected end-to-end path |
| Runner, report, store, promotion, canary, or CLI | focused lifecycle tests including failure/recovery behavior |
| Packaging resource pattern | build a wheel and inspect/install the built artifact |
| Documentation only | validate links, command names, and referenced paths |

Before merging or publishing a repository-wide behavioral change:

```text
python -m ruff check palimpsest tests
python -m pytest -q
python -m pip check
python -m pip wheel . --no-deps --wheel-dir dist-smoke
```

For a release-affecting package change, install the built wheel into a clean
target and invoke the packaged CLI from outside the checkout. Source-tree
success does not prove package resources are present.

A release is not complete when only the happy path passes. Exercise the
relevant boundary failure: malformed record, missing asset, provider failure,
cost ceiling, interrupted resume, stale proposal, failed canary, or rollback,
depending on what changed.

## 13. Experiment state machine

Use these states in reviews and handoffs:

```text
draft
  -> verified
  -> development-run
  -> frozen-challenger
  -> qualification-run
  -> rejected | inconclusive | failed | qualified
  -> proposed
  -> canary-passed
  -> promoted
  -> production-refreshed
  -> rolled-back (when needed)
```

Allowed transitions are evidence-producing actions. Skipping a state requires
an explicit reason grounded in the contract; convenience is not a reason.

A concise experiment handoff should name:

```text
station
hypothesis
baseline candidate path + fingerprint
challenger candidate path + fingerprint
suite path + fingerprint
run ID + report fingerprint
decision and blocking reasons
observed cost and unknown-cost status
proposal/promotion/rollback IDs, when present
next permitted action
```

This is enough for another operator to continue without reconstructing intent
from terminal history.

## 14. Default decisions

When evidence or procedure is ambiguous, use these defaults:

- keep the current production candidate;
- preserve the immutable record;
- create a new version instead of mutating evidence in place;
- use subprocess isolation for decisive runs;
- stop before the next paid dispatch when the budget is uncertain;
- treat missing evidence as unknown, not zero;
- reject stale proposals rather than merging around compare-and-swap;
- roll back by exact recorded identity, never by memory;
- prefer a smaller change with a direct metric over a broad bundle;
- ask the book and source evidence to settle claims that local scores cannot.
