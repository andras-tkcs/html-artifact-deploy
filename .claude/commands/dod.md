---
description: Run the full definition-of-done gate and report a pass/fail table
argument-hint: "[optional: a path to narrow the pytest run]"
---

Run this repo's definition of done — `docs/coding-and-testing-guidelines.md`, "Definition of done"
— and report the result as a table. Run every command from the repo root.

$ARGUMENTS

If arguments were given above, treat them as a narrower pytest target (e.g.
`tests/unit/test_server.py`) and say so in the report; the coverage ratchet is meaningless on a
partial run, so skip it and mark that row `n/a (partial run)` rather than reporting a number that
isn't comparable.

**The blocking gate, in this order** (all of it also blocks in CI — `.github/workflows/tests.yml`):

1. `pytest -v --cov=src/html_artifact_deploy --cov-branch --cov-report=term-missing --cov-report=json:coverage.json`
2. `python3 scripts/check_coverage_floor.py coverage.json`
3. `ruff check .` and `ruff format --check .`
4. `bandit -c pyproject.toml -r src` and `python3 scripts/mypy_strict_modules.py`

The whole-tree `mypy src/html_artifact_deploy` run is informational only — run it if you like, but never
report it as a failure of this gate.

Check the hook's output first: if `.claude/hooks/session-start.sh` printed a `WARNING`, say so in
the report, because a check that ran against the wrong environment is not a pass.

**Then check the conditional rows against the actual diff** (`git diff --stat origin/main...HEAD`),
and for each one that applies, say whether it has been satisfied — do not silently drop it:

- Touched an upstream client (`src/html_artifact_deploy/*_client.py`) or the tools that call it? → a
  `scripts/live_check.py --check <name>` report is owed. No live credentials here: see
  `.claude/skills/steward/SKILL.md` and dispatch `live-check.yml` rather than marking the row done.
- Touched `src/html_artifact_deploy/__main__.py`, transport setup, or anything that can write to stdout? →
  `pytest tests/integration -v`, and `test_stdio_contract.py` must pass.
- Touched a dependency in `pyproject.toml`? → the bound is explained where it is not obvious, and
  for a runtime dependency `build.yml` has been dispatched against the branch (link the run).
- Touched `.github/workflows/`? → a workflow that must be dispatchable is already on `main`, or the
  PR description says it only becomes dispatchable after merge.

**Manual review items.** The definition of done's other "every PR" rows are judgements no command
can make. Read the diff for each one and report it as `ok`, `needs attention` (with the file and
line) or `n/a` — never PASS, since nothing was run:

- Is there a user-visible change? → it needs a line under `CHANGELOG.md`'s `## [Unreleased]`,
  never under a concrete version heading. Internal-only changes don't need one.
- A decision that is hard to reverse, moves a trust boundary, changes the build/release/
  distribution path, or rejects a non-obvious alternative? → it needs an ADR in `docs/adr/`. A
  deleted plan document needs its decisions extracted into ADRs first, or a PR description saying
  it made none (`docs/adr/README.md`).
- Every new or changed tool has a complete description, documented parameters, truthful
  annotations, and returns the minimum.
- No log line or tool error carries a credential, a token or full content.
- New upstream client code has a matching `<Name>ClientError`, caught at the tool boundary.
- New module-level state has a reset in `tests/conftest.py`.
- Comments only where the *why* is non-obvious; no restated-*what* comments; no project history.
- Standing docs (`README.md`'s tool table, `docs/`) describe the new behavior.

**Report** one row per check: the command (or the manual item), PASS/FAIL/`n/a`
(`ok`/`needs attention`/`n/a` for a manual item), and for a failure the actual error — not a
paraphrase. Do not fix anything unless I ask; this command reports, it doesn't repair. End with a
one-line verdict on whether this branch is ready to open or update a PR.
