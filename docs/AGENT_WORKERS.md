# Agent Cells

Palimpsest uses coding-agent harnesses for bounded editorial stations whose work
requires inspecting evidence, zooming page images, and revising a structured
artifact after validation feedback. Agents are executors inside the factory;
they do not plan the product, choose recipe order, or mutate production state.

This is different from an **engineering agent** working on the repository. An
engineering agent uses the source-controlled `palimpsest-experiment` project
skill, changes a candidate, prompt, suite, or station variant in an isolated
Git worktree, and may run the evaluation track. It is initialized by an
operator using
[`OPERATIONS.md` §5.1](OPERATIONS.md#51-initialize-an-isolated-engineering-agent).
It never becomes a production executor merely because it authored an
experiment. Conversely, an agent cell never receives repository-wide authority
or promotes its own output.

## Execution contract

An agent executor receives one resolved cell specification and returns one cell
outcome. The conductor remains responsible for scheduling, freshness, and the
ledger.

For each attempt, `palimpsest/factory/agent_cell.py` recreates an airlocked
workspace under the document's `runs/` directory:

```text
AGENTS.md          station instruction and output contract
evidence/          declared JSON inputs only
images/            declared page images only
out/               required artifact, logs, and session records
```

The agent may read and crop files inside this workspace. It writes exactly the
artifact named by the station contract into `out/`. It does not receive the
repository, factory database, unrelated documents, or credentials through the
workspace.

## Production loop

1. The station stages declared evidence and images.
2. The configured harness runs the station instruction.
3. Palimpsest reads and parses the required output artifact.
4. The station validates schema and domain invariants.
5. If validation fails and repair capacity remains, Palimpsest sends the exact
   rejection into the same agent session.
6. A valid artifact returns to the cell runner; exhausted repairs return a
   structured failure.

Repair is bounded by station options. A missing or invalid artifact never turns
into an empty success.

## Harnesses

Configured executors may invoke OMP or the Codex CLI. Harness-specific
code owns process invocation and session accounting. Station code owns the
instruction, staged evidence, artifact parsing, and domain validation. The
common executor contract keeps the conductor independent of any harness.

Models are recipe settings. Provider authentication comes from the operator's
normal harness login or environment rather than from files staged for the
agent.

## Editorial use

Agent cells are appropriate when the task benefits from an inspect-revise loop,
for example:

- identifying bounded reference evidence for reconstructed sections;
- emending a difficult reading while checking page crops;
- repairing a structured editorial artifact after apparatus validation.

A normal model-gateway call is preferable when one prompt plus declared inputs
can produce and validate the artifact directly. Deterministic image processing,
path resolution, scheduling, and publication never need an agent cell.

## Invariants

- One agent cell corresponds to one station execution.
- Staged evidence matches the station's declared inputs.
- The workspace is recreated for every production attempt.
- Sessions and usage are recorded beneath the cell workspace.
- Validation happens before the artifact enters the canonical library.
- Agents never write the ledger or schedule follow-up stations.
- Repair turns are bounded and preserve the same session context.
