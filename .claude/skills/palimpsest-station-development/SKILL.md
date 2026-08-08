---
name: palimpsest-station-development
description: Add or change a Palimpsest station, implementation variant, artifact contract, recipe slot, workspace path, or localized production fingerprint. Use this skill whenever the user wants to modify one pipeline transformation in code, introduce an alternative algorithm, add a new artifact kind or station, change station inputs or outputs, update production dependencies, regenerate the contract graph, or install a selected production implementation.
---

# Palimpsest Station Development

Use this skill for production architecture and code boundaries. Comparative
evaluation and candidate selection remain in `palimpsest-research`.

## Read First

- `docs/OPERATIONS.md` for the affected production protocol
- `docs/FACTORY.md` sections governing station, conductor, ledger, freshness,
  and workspace behavior affected by the change
- `docs/CONTRACTS.md` for the current live socket graph
- `palimpsest/factory/core/contracts.py`
- `palimpsest/factory/core/station.py`
- `palimpsest/factory/core/registry.py`
- the affected station, recipe, prompt, and focused tests

Use symbol-aware references before modifying exported code when a language
server is available. Reuse the repository's existing station, contract,
workspace, and test patterns rather than creating a parallel convention.

## Decide Variant, Station, or Contract

### New variant

Choose a variant when behavior changes but this socket remains identical:

```text
grain
consumes
optional_consumes
produces
```

Examples: alternative segmentation algorithm, different image preprocessing
implementation, direct multimodal reader versus another reader implementation.

### New logical station

Choose a new station only when there is a distinct transformation and output
concept. One implementation is not evidence for an interface, and a second
implementation of the same transformation is not a second station.

### Contract change

Changing input kinds, optional input semantics, output kind, grain, required
JSON fields, binary format, or workspace location is a contract change. Update
the one canonical contract and path rather than adding an alias or compatibility
shim.

State the classification and why before editing.

## Same-Socket Variant Protocol

1. Preserve the existing logical station name and socket.
2. Give the implementation a stable, meaningful `variant` name.
3. Register it under the existing station.
4. Declare every package-relative behavior-bearing Python source in
   `production_dependencies`.
5. Do not repeat the concrete station module or shared runtime sources already
   included automatically.
6. Verify that old and new variants resolve to distinct implementation
   fingerprints.
7. Add focused tests for registration, socket compatibility, behavior,
   failures, and source-dependency identity.
8. Leave the production recipe unchanged unless this task explicitly installs
   the externally selected production configuration.

The research repository can install this package and evaluate either registered
variant through the same station socket.

## New Station or Contract Protocol

1. Define the artifact kind once in `core/contracts.py`.
2. Define its required fields or binary format explicitly.
3. Derive its one workspace path through `workspace/layout.py`.
4. Implement one station that reads only declared inputs and emits exactly one
   output.
5. Register it; reject unknown kinds, grain mismatch, duplicate producers, and
   incompatible variants before execution.
6. Compose it into the appropriate recipe without teaching the conductor about
   corpus-specific order.
7. Add focused contract, registry, station, cell, conductor, workspace, and
   failure-path tests as affected.
8. Regenerate the graph from live registries:

    ```text
    python -m palimpsest graph --write-docs
    ```

9. Run the generated-document freshness test and an end-to-end manuscript path
   before calling the station production-ready.

Never edit `docs/CONTRACTS.md` manually.

## Station Invariants

A station:

- owns one transformation;
- consumes only declared required and optional artifacts;
- produces exactly one artifact kind;
- never calls a sibling station;
- never schedules work;
- never writes the ledger;
- keeps no mutable state across executions;
- reports structured usage and failure;
- uses atomic workspace I/O;
- validates JSON or binary output before canonical persistence;
- includes every behavior-bearing input in freshness identity.

Page and manuscript grain must agree with the produced artifact contract.
Optional inputs remain fingerprinted when present.

## Localized Implementation Identity

A station fingerprint includes:

- logical station name;
- explicit variant;
- shared production runtime sources;
- concrete station source;
- declared production dependencies.

When importing another Palimpsest module whose code affects behavior, add its
source path and necessary local transitive behavior sources. Do not include
unrelated package files merely to make a test pass; overly broad identity causes
unrelated production invalidation.

Add tests proving:

- changing a declared dependency changes only relevant station identity;
- unrelated package code does not change production identity;
- duplicate, missing, external, or non-Python dependencies fail;
- all built-in station source closures resolve in the installed wheel.

## Recipe and Freshness Boundary

Recipes choose logical station variant, model, prompt, parameters, and options.
The conductor knows ordering and freshness, not why that configuration won.

After an authorized recipe or implementation change, prior production cells are
outdated and require explicit `--refresh STATION`. Development must not delete
artifacts or provenance to simulate that state.

## Research Boundary

This repository supplies executable station sockets, artifact contracts,
localized implementation fingerprints, and production recipes. It does not
store comparison suites, candidate records, promotion history, or research
scorecards. `palimpsest-research` owns that evidence and consumes the installed
factory package.

When research selects a result, install it here as one explicit station,
prompt, or recipe change. Do not add a parallel candidate registry or
production alias.

## Verification

Run the narrowest behavioral proof first, then the changed boundary:

```text
python -m ruff check AFFECTED_PATHS
python -m pytest -q AFFECTED_TESTS
python -m palimpsest graph --write-docs
python -m pytest -q tests/test_factory_contracts.py::test_contracts_doc_is_current
```

For package-data or source-closure changes, build and smoke-test the wheel.

Do not refresh production, commit, or push unless explicitly requested.

## Output Format

Report:

```text
Classification: variant | station | contract
Transformation and socket:
Files and symbols changed:
Production dependency closure:
Implementation fingerprint change:
Contract graph change:
Focused tests:
End-to-end production proof:
Production recipe change, if requested:
Research handoff boundary:
```

Separate observed proof from inferred impact.
