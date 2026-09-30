# Testing policy

What runs where, and when. How to *write* a test is in
[`coding-and-testing-guidelines.md`](coding-and-testing-guidelines.md); the checks a human runs
before a release are in [`release-testing.md`](release-testing.md).

## Principle

**Do not test the full Cartesian product** (`Python version × OS × tool × upstream × client ×
package`). Each dimension is proven on its own, in one layer below, and only a few high-value
end-to-end tests cross a boundary.

## The layers

Every check belongs to exactly one layer. `pyproject.toml`'s `[tool.pytest.ini_options]` registers
the markers; a new test module carries the marker of its layer.

| # | Layer | What it proves | Marker | Where it runs |
|---|---|---|---|---|
| 1 | Unit | Logic in isolation, offline; every tool through the in-process MCP client | `unit` | `tests.yml`, every PR |
| 2 | Integration | The real server process over the real transport (stdio; Streamable HTTP if served), driven by the official MCP client | `integration` | `tests.yml`, every PR |
| 3 | Compatibility | The suite on every supported Python version and OS | (none; a matrix) | `tests.yml`, every PR |
| 4 | Client contract | A real, pinned AI-client CLI configured against the server the way the README says, reporting it connected | `client` | `tests.yml`, every PR, once added |
| 5 | Live upstream | The upstream API still answers in the shape the fixtures record | `live` | `live-check.yml`, self-hosted runner, weekly |
| 6 | Packaged artifact | The built wheel installs into a clean environment and its entry point speaks MCP | `packaged` | `build.yml` |
| 7 | Manual | Subjective judgment; real clients' UI; first-time consent screens | — | [`release-testing.md`](release-testing.md) |

| Failure type | Owning layer |
|---|---|
| Wrong logic or malformed output from a function or tool | 1 |
| A stray `print` on stdout, a crash at start-up, a broken entry point | 2 |
| Works on 3.13 / Linux, breaks on 3.11 or Windows | 3 |
| A client version stops accepting the server's configuration or tool schemas | 4 |
| The upstream service renamed a field or changed a response | 5 |
| The wheel is missing a file, or its dependencies do not resolve | 6 |
| A tool description reads badly in a real client | 7 |

## Layers 1–4: every PR

`.github/workflows/tests.yml` runs on every pull request, every push to `main` or `releases/**`,
and on dispatch. A 100% pass rate is required to merge.

| Job | Runs |
|---|---|
| `test` (Ubuntu, Python 3.13) | The full suite with branch coverage; `scripts/check_coverage_floor.py coverage.json`; uploads `coverage-report` |
| `Test (Python 3.x)` | The suite on the other supported Python versions, one check each |
| `Test (windows-latest)`, `Test (macos-latest)` | The suite on Windows and macOS (not required checks by default; see the workflow's comment) |
| `static-analysis` | `ruff check .`, `ruff format --check .`, `bandit`, `scripts/mypy_strict_modules.py` (all blocking); whole-tree `mypy` (informational) |

**Coverage is a ratchet.** `scripts/check_coverage_floor.py` fails if overall coverage, or any
module on its list, drops below its recorded floor. Raise a floor in the PR that raises its
coverage; lowering one is a regression, not a config edit.

**Required checks.** `REQUIRED_STATUS_CHECKS` in `scripts/update_branch_protection.py` is the
reviewed list. Update it in the PR that adds, renames or removes one of those jobs; a maintainer
applies it by hand (`... apply`). `... show` compares it with what is live.

### Client contract tests (layer 4)

Add these once the server has users on a specific client. A real, unmodified client CLI is
configured against the server exactly as the README tells a user to, and must report it connected
and list its tools. Pin each CLI to an exact version in `tests/client/package.json` with a
committed lockfile, install it with `npm ci` in the `test` job, and run it under a throwaway
`HOME` with every vendor credential variable removed: these tests must never need a vendor
account. A weekly canary workflow can run the same tests against each client's `@latest` and open
an issue when one breaks; it gates nothing.

## Layer 5: live upstream

Live credentials exist only on the project's self-hosted runner, as local files — never as GitHub
Actions secrets, never on a GitHub-hosted runner, never reachable from a `pull_request` trigger
(ADR 0007). Setup is [`live-qa.md`](live-qa.md).

- **`live-check.yml`** — weekly and on dispatch. Runs `scripts/live_check.py --check` over every
  registered check. A failure is provider drift or an expired QA grant; both need a person.
- **`qa-record-fixture.yml`** — dispatch only, input `name`. Records one fixture and commits it to
  the branch it was dispatched against; refuses `main` and `releases/**`. Shares the `live-check`
  concurrency group, so it queues behind a running check.
- **`/qa-record <name>`** dispatches `qa-record-fixture.yml` for the current branch, then pulls
  and reviews the fixture diff.

A PR touching an upstream client owes a `--check` report for it, from a QA machine or a dispatched
`live-check.yml` run.

## Layer 6: packaged artifact

`build.yml` builds the sdist and wheel, installs the wheel into a fresh virtual environment with
none of the repository on the path, and runs the `packaged` smoke test against the installed
entry point, before anything is uploaded. It runs on a `v*` tag push and on dispatch; every
publish step is gated on a tag ref, so a dispatched run builds and tests everything and publishes
nothing. That dispatched run is the release pre-flight (ADR 0006).

## Layer 7: manual

The manual release checks, and the rule for what may stay manual, are in
[`release-testing.md`](release-testing.md).

## Running the gate yourself

- **`/dod`** (`.claude/commands/dod.md`) runs the blocking gate of the
  [definition of done](coding-and-testing-guidelines.md#definition-of-done), then checks the diff
  for the conditional items and reports a pass/fail table. Given a path, it narrows `pytest` and
  skips the coverage floor.
- **`scripts/pre_release_check.py`** runs the same blocking commands as one script and exits
  non-zero on any failure.
