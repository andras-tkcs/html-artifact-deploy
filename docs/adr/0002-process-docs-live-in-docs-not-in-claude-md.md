# ADR 0002: Process documentation lives in `docs/` and `CONTRIBUTING.md`, not in `CLAUDE.md`

## Status

Accepted — 2026-09-30. Inherited from the MCP server template.

## Context

`CLAUDE.md` is loaded into every Claude Code session, which makes it tempting to put the whole
process there: releasing, branch naming, the ADR lifecycle. But that content is what every
contributor needs, human or not, and a file named after one tool is neither where people look for
it nor something the project should depend on keeping.

## Decision

1. Branch naming, the changelog rule, `releases/*` branches and the plan/ADR summary are in
   `CONTRIBUTING.md`; releasing is `docs/releasing.md`; code, test and done rules are
   `docs/coding-and-testing-guidelines.md` and `docs/testing-policy.md`.
2. `CLAUDE.md` holds only what is specific to Claude Code sessions (its commands and skills, the
   worktree convention) and points to those documents for everything else.
3. Nothing outside `.claude/` cites `CLAUDE.md` as the authority for a process.

## Alternatives considered

- **Keep `CLAUDE.md` as the reference and link to it from the docs.** Rejected: the process would
  still depend on a tool-specific file, and moving the tool's configuration later would take the
  process documentation with it.

## Consequences

- A process change edits `docs/` or `CONTRIBUTING.md` in the same PR as the behavior.
- `CLAUDE.md` and `.claude/` can be moved or removed without touching the process documentation.

## Verification

`CLAUDE.md` links out for every process; `CONTRIBUTING.md` and `docs/releasing.md` hold it.
