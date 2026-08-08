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
`pytest`, `ruff`, `setuptools`, and `wheel`.

## Test conventions

The suite is deliberately hermetic: **zero network and zero model calls**.
Every external dependency is mocked, monkeypatched, or served from a
loopback fixture server:

- Network access is replaced with recorded/fake sessions or a
  `ThreadingHTTPServer` fixture (`tests/test_factory_evaluation_assets.py`).
- Model calls never leave the process; gateways and executors are fakes.
- The RF-DETR runtime is never launched; its worker and `_predict` are
  monkeypatched (`tests/test_factory_align_rfdetr.py`).
- The content-addressed evaluation asset cache
  (`palimpsest/factory/evaluation/assets/`, gitignored) is not needed by the
  test suite at all — it exists only for live `palimpsest bench run`
  executions.

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

The de-facto workflow is the source-controlled project skills under
`.claude/skills/`. Invoke a skill explicitly when intent could span
experiment, production, and promotion boundaries:

| Skill | Use it for |
|---|---|
| `palimpsest-experiment` | Design, initialize, run, resume, or review one bounded station experiment |
| `palimpsest-production-ops` | Intake, adopt, run, refresh, recover, publish, and inspect manuscripts |
| `palimpsest-promotion` | Qualification, proposal, canary, promotion, explicit production refresh, and rollback |
| `palimpsest-station-development` | Station variants, new transformations, artifact contracts, and implementation fingerprints |

The skills encode procedure and safety boundaries; `docs/OPERATIONS.md` and
the live contracts remain the sources of truth.

## Evaluating without paying

The checked-in fixtures let you exercise the evaluation plane at zero cost.
Inventory the tracked candidates, suites, and judges:

```bash
python -m palimpsest bench list
```

Resolve one suite against the local object cache:

```bash
python -m palimpsest bench verify \
  --suite palimpsest/factory/evaluation/suites/align/conformance-v1.yaml
```

Suites whose cases declare external `iiif:` sources (for example the `read`
development suites) are verified against the content-addressed object cache
(`library/evaluations/objects`). On a fresh clone, populate that cache first —
`bench fetch` downloads the declared sources read-only and verifies every
byte against its pinned sha256:

```bash
python -m palimpsest bench fetch \
  --suite palimpsest/factory/evaluation/suites/read/zh-vatican-borg-cin-361-f004r-development-v1.yaml
```

`bench fetch` requires network access to the source archives (Vatican
Digital Library, Gallica) and records no paid work.

## Branch and commit conventions

- Work on a topic branch; open a pull request against `master`.
- Commit and PR attribution is **Dexin Huang <dh3172@columbia.edu>**.
- Commit messages state the observable change and its boundary (for example
  "add spread-safe deframe variant with passthrough option" rather than
  "wip").
- Do not commit generated output, the ledger (`library/`), the static site,
  the asset cache, or `.env` — `.gitignore` keeps them local.
- One artifact kind, one contract, one path; prompts are files and are
  content-hashed; paid work never reruns implicitly after configuration
  drift. If a change would violate one of the design rules in the README,
  say so in the pull request and justify it.

## CI

Every push and pull request runs `ruff check .` and the hermetic pytest
suite on Python 3.11 and 3.12 (see `.github/workflows/ci.yml`). There is no
fetch step and no network access: if your change makes the suite depend on
the asset cache or a live model runtime, the test is wrong, not the CI.
