---
name: steward
description: HTML Artifact Deploy repo policy for an agent session — which work has to be dispatched to a GitHub Actions runner instead of run locally, which pull requests to follow, and how to treat a red check. Read this before acting on a CI failure or a review comment.
---

# Stewarding a HTML Artifact Deploy pull request

This is repo-specific policy. It sits alongside `CONTRIBUTING.md` and `docs/releasing.md` (branch
hygiene and release mechanics), `docs/coding-and-testing-guidelines.md` (code and test conventions,
and the definition of done) and `docs/testing-policy.md` (which layer runs where) — none of which it
replaces. What it adds is what a session should do when the box it runs on cannot do the thing
being asked.

## Work that has to leave this machine

A Claude Code on the web container has no macOS or Windows, and — by deliberate policy, not by
oversight — no live upstream credentials (ADR 0007). None of that is a reason to skip a checklist
row. Each of those jobs exists as a workflow with `workflow_dispatch`, and a session can dispatch
it **against its own branch** and read the result back.

| You need | Dispatch | Notes |
|---|---|---|
| A fixture for a newly added upstream endpoint | `qa-record-fixture.yml` | Input `name`, a key of `CHECKS` in `scripts/live_check.py`. Runs on the self-hosted runner and **commits the fixture back to the branch it was dispatched against** — pull before you continue. See `/qa-record`. |
| A `live_check.py --check` report for the definition of done | `live-check.yml` | No inputs. Link the run in the PR rather than pasting nothing. |
| The built wheel, and its packaged smoke test on Linux, Windows and macOS | `build.yml` | No inputs. Publishes nothing off a tag. |
| Cross-platform or cross-version `pytest` | — | Don't dispatch. `tests.yml` already runs every leg on every PR; read the failing job's log instead of guessing. |
| To cut a release tag | `release.yml` | Inputs `version` and `dry_run`. **`dry_run` defaults to true** — dispatch it that way first. Only cut for real when the maintainer has seen the dry run and said to. See `/cut-release`. |

Things about dispatching, so they are not rediscovered at runtime:

- **The workflow must already be on `main`.** GitHub only offers `workflow_dispatch` for workflows
  present on the default branch; the `ref` you pass selects which checkout runs. A workflow added
  on a feature branch is not dispatchable until that branch merges.
- **`qa-record-fixture.yml` and `live-check.yml` share a concurrency group** with
  `cancel-in-progress: false`, because both read and refresh the same credential files. A queued
  run is correct behaviour, not a hang. Wait; do not re-dispatch.
- **A recorded fixture is still a diff to read.** No real account identifier, e-mail, tenant URL,
  token or private content may enter the repository, however the fixture was produced.
- **Pushing a release tag directly is not possible here** — the container can push branches but
  not `refs/tags/*`. Don't try; a failed push can leave a local tag behind that makes
  `tag_release.py`'s later checks lie.

## Which pull requests to follow

- **A PR this session opened is this session's to drive to green.** Subscribe to it, and keep
  working it until CI passes and it is mergeable.
- **Do not follow Dependabot PRs** unless the maintainer asks. A dependency bump is a supply-chain
  decision for a person.
- **Stop at merge or close.** Nothing further is owed.

## A red check is real until proven otherwise

A second red run on the same commit is real and must not be re-run away. In particular:

- Never skip, disable, `xfail` or quarantine a test to get to green. The suite is a 100%-pass,
  ratcheted-coverage gate (`scripts/check_coverage_floor.py`), and a hole in it does not show up
  again until it matters.
- Never push an empty commit, or close and reopen a PR, to kick CI.
- A coverage-floor failure means the new code needs tests, not that the floor needs lowering.
- A `bandit` finding that is genuinely a false positive gets `# nosec BXXX  # <reason>` at the
  call site — never a suppression in `pyproject.toml`.
- A missing live credential is **not** a test failure. It is the dispatch table above.

## Conventions worth restating because they are easy to get wrong

- **Never open a concrete `## [X.Y.Z]` heading in `CHANGELOG.md` on a feature branch.** Entries go
  under `## [Unreleased]`; the release build fails on a duplicated or still-populated section.
- **PRs merge with a real merge commit, not a squash.** Every commit message on the branch
  survives into `main`'s history, so write each one for that audience.
- **Branch names.** `CONTRIBUTING.md` specifies `<type>/<kebab-case-description>`. A Claude Code on
  the web session is assigned a `claude/<generated-name>` branch it cannot rename; use it and put
  the `<type>` in the PR title instead. Apply the convention normally anywhere it can be followed.
- **`releases/*` is protected like `main`.** If work targets one, branch from it and PR back into
  it — not into `main`.
- **No project history in code or docs** (ADR 0008): no phase names, plan IDs or bare issue
  numbers in comments; `tests/unit/test_no_project_history.py` fails on them.
