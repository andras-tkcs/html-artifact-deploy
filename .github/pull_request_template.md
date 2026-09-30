## What this changes

<!-- Why the change is needed, not just what it does. -->

## Definition of done

The checklist from [`docs/coding-and-testing-guidelines.md`, "Definition of done"](../docs/coding-and-testing-guidelines.md#definition-of-done),
which is the authoritative copy. Tick what you ran; strike through anything genuinely not
applicable with a one-line reason rather than leaving it blank. `/dod` runs the commands.

### Every PR

- [ ] `pytest -v --cov=src/html_artifact_deploy --cov-branch --cov-report=term-missing --cov-report=json:coverage.json`
      passes at 100%, and `python3 scripts/check_coverage_floor.py coverage.json` passes.
- [ ] `ruff check .`, `ruff format --check .`, `bandit -c pyproject.toml -r src` and
      `python3 scripts/mypy_strict_modules.py` pass.
- [ ] A user-visible change has a line under `CHANGELOG.md`'s `## [Unreleased]` (never under a
      version heading).
- [ ] A decision that is hard to reverse, moves a trust boundary, changes the build/release path,
      or rejects a non-obvious alternative has an ADR in `docs/adr/`. A PR that deletes a plan
      document extracts its decisions into ADRs first, or says below that it made none.
- [ ] Every new or changed tool has a complete description, documented parameters, truthful
      annotations, and returns the minimum.
- [ ] No log line or tool error carries a credential, a token or full content.
- [ ] New upstream client code has a matching `<Name>ClientError`, caught at the tool boundary.
- [ ] New module-level state has a reset in `tests/conftest.py`.
- [ ] Comments only where the *why* is non-obvious; no project history in code or docs.
- [ ] Standing docs describe the new behavior.

### Only if this PR touches those files

- [ ] **An upstream client or the tools that call it** — `scripts/live_check.py --check <name>`
      report pasted below, or a dispatched `live-check.yml` run linked.
- [ ] **`__main__.py`, transport setup, or anything that writes to stdout** —
      `pytest tests/integration -v` run; `test_stdio_contract.py` passes.
- [ ] **A dependency in `pyproject.toml`** — bounds explained where not obvious; for a runtime
      dependency, a dispatched `build.yml` run linked.
- [ ] **`.github/workflows/`** — dispatchable workflows are on `main`, or this PR says they become
      dispatchable after merge.

## Live check report

<!-- Paste the `live_check.py --check` report or link the run if the row above applies; delete
this section otherwise. A cloud session has no credentials: dispatch the workflow instead. -->

## Notes for review

<!-- Anything a reviewer should look at first, or a decision worth a second opinion. -->
