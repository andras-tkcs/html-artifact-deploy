# Contributing to HTML Artifact Deploy

## Pull requests

All changes to `main` go through pull requests. Direct pushes are blocked.

1. Create a branch off `main`, named `<type>/<kebab-case-description>`, with `feature/`, `fix/`,
   `chore/`, `docs/` or `tests/` as the type. `plan/` branches hold a plan document while its work
   is open (see [Decisions, plans and ADRs](#decisions-plans-and-adrs)) and are never PR'd.
2. Keep PRs focused: one logical change per PR. PRs merge with a merge commit, not a squash, so
   every commit message on your branch ends up in `main`'s history. Write each one for that
   audience.
3. Describe *why* the change is needed, not just what it does.
4. PRs require review and approval from a code owner (`.github/CODEOWNERS`) before merging.
5. A user-visible change adds a line under `CHANGELOG.md`'s `## [Unreleased]` heading in the same
   PR. Never open a concrete `## [X.Y.Z]` heading on a feature branch; only the PR that prepares a
   release does that — see [`docs/releasing.md`](docs/releasing.md#release-notes-come-from-changelogmd).
6. Before opening a PR, work through the
   [definition of done](docs/coding-and-testing-guidelines.md#definition-of-done) and fill in the
   PR template's checklist. [`docs/testing-policy.md`](docs/testing-policy.md) says which checks CI
   runs and which you run or dispatch yourself.

### Release integration branches (`releases/*`)

`releases/*` (for example `releases/1.2-dev`) is a long-lived integration branch, cut from `main`
only when a batch of work for the next release has to accumulate somewhere other than `main`
while `main` stays frozen for a prior release's remaining blockers. It is a deliberate exception,
not a standing convention: while one exists, branches fork from it and PR into it with the normal
naming, and the `releases/*` branch is deleted once it merges back into `main` in one PR.

`releases/*` is protected like `main` (`scripts/update_branch_protection.py --branch <name>
apply`), and every workflow push trigger scoped to `main` for a test or lint job also lists
`"releases/**"`. Publishing triggers stay `main`- and tag-only.

## Decisions, plans and ADRs

Three kinds of document, three lifecycles. The full rules, template and index are in
[`docs/adr/README.md`](docs/adr/README.md):

- **Plans** (what we are about to do) are temporary: a GitHub issue, or a `docs/*-plan.md` while
  its work is open. They are deleted when the work lands.
- **ADRs** (`docs/adr/NNNN-*.md`: why it is this way, what was rejected) are permanent and frozen
  once accepted. Change your mind with a new ADR that supersedes the old one; never rewrite an
  accepted ADR's body, and never put implementation progress in one.
- **Reference docs** (`docs/*.md`, this file) describe today's behavior and link to ADRs for the
  *why* rather than retelling it.

**Retiring a plan requires extracting its decisions first.** The PR that deletes a plan document
adds or amends an ADR for every decision the plan made that meets the ADR bar in
[`docs/adr/README.md`](docs/adr/README.md#when-a-decision-needs-an-adr), or says in its
description that the plan made none.

The same applies to a decision made anywhere else (a PR thread, an issue, a reference doc): if it
meets the bar, it gets an ADR in the same PR. ADRs link to issues, PRs, commits and source files,
never to a plan document, which will not outlive it.

## Releasing

Cutting a release is a git tag, not a commit; everything else follows from the tag push. The whole
process is [`docs/releasing.md`](docs/releasing.md).

## Issues

Use GitHub Issues for bug reports and feature requests. Include the version
(`html-artifact-deploy --version`), your OS and Python version, the MCP client you connect with, and steps
to reproduce. Do not report a security vulnerability in a public issue — follow
[`SECURITY.md`](SECURITY.md).

## Code style

- Python 3.11+, standard library preferred over new dependencies. A new runtime dependency that
  breaks that rule gets an ADR saying why.
- No comments unless the *why* is non-obvious; match the surrounding code.
- The full conventions are
  [`docs/coding-and-testing-guidelines.md`](docs/coding-and-testing-guidelines.md).

## Running from source

```bash
python3 -m venv .venv && .venv/bin/pip install -e ".[dev]"
.venv/bin/html-artifact-deploy            # speaks MCP on stdin/stdout
.venv/bin/python scripts/pre_release_check.py
```
