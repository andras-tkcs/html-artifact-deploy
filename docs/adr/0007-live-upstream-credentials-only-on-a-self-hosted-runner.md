# ADR 0007: Live upstream credentials exist only on a self-hosted runner

## Status

Accepted — 2026-09-30. Inherited from the MCP server template. Applies once the server talks to an
upstream service.

## Context

Fixtures recorded from the upstream service go stale when the provider changes its API, so
something has to call the real service regularly. That needs a real credential. A GitHub Actions
secret is exposed to every workflow that can read it, and a workflow triggered by
`pull_request` runs code from the pull request.

## Decision

1. Live QA credentials are files on a self-hosted runner the project controls, for a dedicated QA
   account, never GitHub Actions secrets and never on a GitHub-hosted runner.
2. Workflows that use them (`live-check.yml`, `qa-record-fixture.yml`) run on `schedule` and
   `workflow_dispatch` only, never on `pull_request`, and share one concurrency group.
3. Every other test replays committed, redacted fixtures offline.

## Alternatives considered

- **Encrypted Actions secrets.** Rejected: readable by any workflow a maintainer (or a compromised
  action) runs, and tempting to use from `pull_request`.
- **No live checks.** Rejected: provider drift would first show up as a user's bug report.

## Consequences

- A cloud session never has credentials; it dispatches the workflow (`/qa-record`).
- Someone has to keep the runner and the QA grant alive; an expired grant is a person's job.

## Verification

`.github/workflows/live-check.yml` and `qa-record-fixture.yml` (triggers, `runs-on`, concurrency);
`docs/live-qa.md`.
