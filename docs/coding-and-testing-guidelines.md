# Coding and testing guidelines

The repository's standing expectations for implementation and test changes. Keep this document
descriptive: when a pattern becomes established in `src/html_artifact_deploy/` and `tests/`, write it down
here; when a rule here stops matching the code, fix one or the other in the same PR.

See [`CONTRIBUTING.md`](../CONTRIBUTING.md) for process (branches, PRs, ADRs) and
[`testing-policy.md`](testing-policy.md) for which test runs where. This document covers how to
write, test and extend the code.

## Coding guidelines

Keep policy and security decisions in shared layers rather than per-tool shortcuts: a new tool
uses the same validation, error, logging and upstream-client mechanisms as the existing ones.
Prefer explicit, testable boundaries over hidden global state: keep filesystem, network and
upstream-API behavior behind modules that can be exercised on their own.

### Language baseline

- Python 3.11+. Every module starts with a docstring, then `from __future__ import annotations`.
- Prefer the standard library over new dependencies. A runtime dependency that is a deliberate
  exception gets an ADR (ADR 0003 is the model).
- Modern union syntax, `X | None`, never `Optional[X]`.
- Type-hint every function signature, including the return type. Dataclass fields are typed.

### Module and docstring conventions

- Every module has a docstring, even a one-liner. A module with a non-obvious lifecycle or
  invariant (threading, ordering, what a caller must not assume) explains the *why* there.
- Default to no comments. Add one only for a non-obvious *why* — a hidden constraint, a race that
  was fixed, a workaround for an upstream quirk — never to restate *what* the next line does.
- Comments, docstrings and user-visible strings carry no project history: no phase names, plan or
  review item IDs, bare issue or PR numbers, or "as of version N" phrasing. Say the reason in your
  own words; when the reason is a decision, name its ADR (`ADR 0003`). Open work is cited by the
  issue's full URL, with the limitation described next to it. History belongs in `CHANGELOG.md`
  and the ADRs. `tests/unit/test_no_project_history.py` enforces this (ADR 0008).

### MCP tools

The tool list is the server's public API: an AI client chooses and calls tools from their names,
descriptions and schemas alone.

- **One factory.** Every tool is registered in `server.build_server()` (or a function it calls),
  so every transport and every test gets the same server.
- **Descriptions are written for the model.** A one-sentence summary first; then what the tool
  returns (fields, limits, paging); then which related tool to use instead, when there is one.
  Every parameter is documented: its format, where the value comes from, and what empty means.
- **Names are stable.** Renaming or removing a tool, or a parameter, breaks every client prompt
  and saved configuration that uses it. It is a user-visible change with a `CHANGELOG.md` line,
  and usually an ADR.
- **Annotations are truthful.** A tool that only reads says so (`readOnlyHint`); a tool that
  deletes says so (`destructiveHint`). Hints are not a security boundary, but clients use them to
  decide when to ask the user.
- **Return the minimum.** A tool returns what the caller needs, not the whole upstream payload.
  Anything that crosses the MCP boundary can end up in a model's context and its logs.
- **Errors are for the caller.** A failure the caller can act on (not found, bad argument,
  permission denied) is returned as a tool error with a message saying what to do. An unexpected
  failure is logged with its traceback on stderr and returned as a short, generic error; a raw
  exception, a stack trace or a credential never reaches the client.

### Upstream clients and error handling

- Every client for an external API (`src/html_artifact_deploy/<name>_client.py`) defines its own
  `<Name>ClientError(Exception)` and raises only that across its public methods.
- Tools catch the client's error type at the boundary and turn it into a tool error; they never
  let it, or a bare `except Exception`, leak raw into the response.
- A non-critical side effect (a metrics write, a cache update) is wrapped in its own
  `try/except Exception: logger.warning(...)`, so it never fails the primary operation.

### Untrusted input

- Every argument a client sends is untrusted, and so is every value that came from the upstream
  service. Validate at the boundary; fail closed on anything malformed.
- A path, filename or identifier that ends up on the filesystem is sanitized first
  (`os.path.basename(name) or "<fallback>"` is the minimum), and the destination is computed once
  and reused, never recomputed.
- Never build a shell command, SQL statement or URL by string concatenation from client input.

### Async and concurrency

- A blocking call (an SDK without async support, file I/O on a hot path) is wrapped in
  `asyncio.to_thread(...)`, never called directly from an `async def`.
- Every network call has a timeout.

### Logging

- Library code logs through `logging.getLogger(__name__)`.
- **Over the stdio transport, stdout is the protocol channel.** Nothing may `print()` to stdout
  once the server runs; logs go to stderr. A stray `print` corrupts the stream and the client
  disconnects. `tests/integration/test_stdio_contract.py` catches this.
- Never log a credential, a token, or the full content the server moves (a message body, a
  document). Log identifiers and counts.

### Formatting and static analysis

```bash
ruff check .
ruff format --check .
python3 scripts/mypy_strict_modules.py
bandit -c pyproject.toml -r src
```

`pyproject.toml` is authoritative for Ruff, mypy, Bandit, pytest and coverage configuration. Ruff,
Bandit and the promoted-module mypy run block CI. The whole-tree `mypy src/html_artifact_deploy` run is
informational; `scripts/mypy_strict_modules.py` re-runs mypy, blocking, over the modules promoted
with a `[[tool.mypy.overrides]]` block. Promote a module once it is clean; from then on a regression
in it fails the merge. A Bandit finding that is a genuine false positive gets
`# nosec BXXX  # <reason>` at the call site, never a suppression in `pyproject.toml`.

## Testing guidelines

### Framework and layout

- `pytest` with `pytest-asyncio` (`asyncio_mode = "auto"`: no `@pytest.mark.asyncio` needed) and
  `pytest-timeout` (30 s per test, so a hung fixture fails loudly instead of stalling CI).
- `tests/unit/` mirrors `src/html_artifact_deploy/`: `src/html_artifact_deploy/<module>.py` is tested by
  `tests/unit/test_<module>.py`.
- `tests/integration/` holds tests that run the real server over a real transport.
- Every test module carries the marker of its layer (`unit`, `integration`, `client`, `live`,
  `packaged`), declared in `pyproject.toml`.
- Run the smallest relevant test set while developing, then `/dod` before opening or updating a
  PR. A 100% pass rate is required to merge.
- Unit and integration tests are offline and deterministic. No real credentials and no unmocked
  upstream calls in GitHub-hosted CI.

### Module and class organization

- Every test module opens with a docstring naming the module under test and, where relevant, the
  one invariant that matters most.
- Group tests into `class TestScenario:` blocks by behavior, not in one flat list. Bare
  module-level `def test_...` functions are for structural, cross-cutting checks.
- A regression test's docstring explains the bug it guards against, so a later reader can tell
  why it exists before deciding it is safe to delete or weaken.

### Fixtures and isolation

- Module-level state is reset in the autouse `_reset_singletons` fixture in `tests/conftest.py`,
  before and after each test. Any new module-level state gets a matching line in its `_reset()`
  in the same PR, or it leaks between tests silently.
- Use `tmp_path` for anything that touches the filesystem; never point a test at a real
  configuration or data directory.

### Faking the upstream service

- Tool tests stub the upstream *client* (its public methods), not the HTTP library underneath it,
  and assert on what the tool sent to it and what it returned.
- Client tests replay committed, redacted live fixtures from `tests/fixtures/live/<name>/` through
  the real parser, in a `TestLiveFixtureParsing` class. They must not need a network connection.

### Test design

Prefer tests that assert observable contracts over implementation trivia. For a tool, check the
complete relevant outcome: what it returned (or the error it raised), what it sent upstream, and
any side effect. Parameterize where several inputs share one invariant.

Never synchronize with a fixed sleep when an event, a readiness check or an explicit signal can
make the test deterministic. Every async or subprocess test fails in bounded time.

### Security-sensitive changes

Changes to authentication, credential storage, input validation, or what data a tool returns need
targeted negative tests as well as the happy path. Fail closed on malformed or unknown
configuration, and prove it with a test that expects the rejection.

### Live fixtures and upstream drift

Real upstream checks run through `scripts/live_check.py` (`--check`, `--record`) against a
dedicated QA account, on the self-hosted runner (`live-check.yml` weekly or dispatched,
`qa-record-fixture.yml` to record one fixture onto a branch). See
[`live-qa.md`](live-qa.md). Never commit a real account identifier, token, tenant URL, private
content or an unredacted upstream payload in a fixture.

### Definition of done

This section is the authoritative copy. `/dod` (`.claude/commands/dod.md`) runs its commands and
checks the conditional rows against the branch's diff, and `.github/pull_request_template.md`
repeats the checklist for the PR description.

**Every PR:**

- [ ] `pytest -v --cov=src/html_artifact_deploy --cov-branch --cov-report=term-missing
      --cov-report=json:coverage.json` passes at 100%, and
      `python3 scripts/check_coverage_floor.py coverage.json` passes (the coverage ratchet).
- [ ] `ruff check .`, `ruff format --check .`, `bandit -c pyproject.toml -r src` and
      `python3 scripts/mypy_strict_modules.py` all pass.
- [ ] A user-visible change has a line under `CHANGELOG.md`'s `## [Unreleased]` heading, never
      under a concrete version heading. Internal-only changes don't need one.
- [ ] A decision that is hard to reverse, moves a trust boundary, changes the build/release/
      distribution path, or rejects a non-obvious alternative has an ADR in `docs/adr/`. A PR that
      deletes a plan document extracts that plan's decisions into ADRs first, or says in its
      description that it made none.
- [ ] Every new or changed tool has a complete description and documented parameters, truthful
      annotations, and returns the minimum (see "MCP tools").
- [ ] No log line or tool error carries a credential, a token or full content.
- [ ] New upstream client code has a matching `<Name>ClientError`, caught at the tool boundary.
- [ ] New module-level state has a reset in `tests/conftest.py`.
- [ ] Comments only where the *why* is non-obvious; no restated-*what* comments; no project
      history.
- [ ] Standing docs describe the new behavior.

**Only if the PR touches these files:**

- [ ] **An upstream client or the tools that call it**: `scripts/live_check.py --check <name>`
      against the QA account, report pasted into the PR. Without local QA credentials, dispatch
      `live-check.yml` against the branch and link the run instead.
- [ ] **`__main__.py`, the transport setup, or anything that writes to stdout**:
      `pytest tests/integration -v` run locally, and `test_stdio_contract.py` passes.
- [ ] **A dependency in `pyproject.toml`**: the change is deliberate, the version bounds are
      explained in a comment when they are not obvious, and the `packaged` smoke test in
      `build.yml` has been dispatched against the branch when the dependency is a runtime one.
- [ ] **`.github/workflows/`**: a workflow that must be dispatchable is already on `main`, or the
      PR says it becomes dispatchable only after merge.

## Adding a tool

1. Register it in `server.build_server()` with a complete description (see "MCP tools").
2. If it calls an upstream service, put the calls in a client module with its own error type, and
   add a `LiveCheck` for the endpoint to `scripts/live_check.py`'s `CHECKS`.
3. Tests: the tool's happy path, each error it can return, and that it appears in `list_tools`
   with the expected schema — in `tests/unit/test_<module>.py`. Update the tool-list assertions in
   `tests/unit/test_server.py`.
4. Record a fixture for any new upstream endpoint (`/qa-record <name>`), and add
   `TestLiveFixtureParsing` for it.
5. Add the tool to `README.md`'s tool table, and a `CHANGELOG.md` line.

## Documentation

Update standing documentation in the same PR as the behavior. Standing docs describe current
behavior, not implementation history: no phase narratives, issue numbers, version qualifiers or
"previously / after X" explanations. History belongs in `CHANGELOG.md` and the *why* in an ADR;
see [`adr/README.md`](adr/README.md) for when a plan, an ADR or a reference doc is the right home.

A new testing gap belongs in [`testing-policy.md`](testing-policy.md) if it changes current
policy, or in [`release-testing.md`](release-testing.md)'s "What stays manual" if it is a standing
open item — not in a plan document written to hold it.
