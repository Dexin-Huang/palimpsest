# Contributing to Palimpsest

Palimpsest is a provenance-first production factory for recovering readable
books from manuscript images. The repository is public, the ledger is
append-only, and paid model calls are the currency — changes that weaken
evidence, repeat paid work, or blur the diplomatic layer are rejected in
review.

## Development setup

Palimpsest requires Python 3.11 or newer. Install it in a repository-local
virtual environment so unrelated user packages cannot affect the factory.

Windows PowerShell:

```powershell
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade --editable ".[dev]"
```

macOS or Linux:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade --editable ".[dev]"
```

Verify the environment with `python -m pip check`. The `dev` extra installs
`build`, `pytest`, `ruff`, `setuptools`, and `wheel`.

## Test conventions

The suite is deliberately hermetic: **zero network and zero model calls**.
External dependencies use recorded responses, fakes, or local fixture servers.
Model gateways and agent executors stay in-process, and RF-DETR process
launches are replaced with deterministic test doubles.

Run the suite:

```bash
python -m pytest -q
```

`testpaths = ["tests"]` in `pyproject.toml`; add new tests there and keep the
hermeticity: a test that opens a socket to the outside world or shells out to
a paid lane will be sent back.

Lint with the same tool CI uses:

```bash
ruff check .
```

Keep the codebase clean under ruff; fix unused imports and dead code rather
than adding `noqa` comments.

## Working on the factory

Use the source-controlled project skills under `.claude/skills/`:

| Skill | Use it for |
|---|---|
| `palimpsest-production-ops` | Intake, adopt, run, refresh, recover, publish, and inspect manuscripts |
| `palimpsest-station-development` | Station variants, transformations, artifact contracts, and implementation fingerprints |

`docs/OPERATIONS.md` and the generated contract graph remain the production
sources of truth. Research and candidate evaluation belong in
`palimpsest-research`; install a selected implementation here by changing the
station or its recipe slot.

## Branch and commit conventions

- Work on a topic branch; open a pull request against `master`.
- Commit and PR attribution is **Dexin Huang <dh3172@columbia.edu>**.
- Commit messages state the observable change and its boundary (for example
  "add spread-safe deframe variant with passthrough option" rather than
  "wip").
- Do not commit generated output, the ledger (`library/`), the static site, or
  `.env`; `.gitignore` keeps them local.
- One artifact kind, one contract, one path; prompts are files and are
  content-hashed; paid work never reruns implicitly after configuration
  drift. If a change would violate one of the design rules in the README,
  say so in the pull request and justify it.

## CI

Every push and pull request runs `ruff check .` and the hermetic pytest suite
on Python 3.11 and 3.12 (see `.github/workflows/ci.yml`). There is no live
model or external-network step.
