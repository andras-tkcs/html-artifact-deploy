# ADR 0008: Code and standing docs carry no project history

## Status

Accepted — 2026-09-30. Inherited from the MCP server template.

## Context

Plans and reviews name things: `Phase 3`, `P2`, a review item `F5`, issue `#41`. Those names leak
into comments and docs while the work is in flight, and outlive the plan that defined them. A
later reader cannot tell a phase name from a feature name, or a closed issue from an open one.

## Decision

Comments, docstrings, user-visible strings and standing docs say the reason in their own words.
When the reason is a decision, they name its ADR. Open work is cited by the issue's full URL, with
the limitation described next to it. History lives in `CHANGELOG.md`, in commit messages and in the
ADRs. `tests/unit/test_no_project_history.py` enforces the recognizable shapes.

## Alternatives considered

- **Allow references while the plan exists.** Rejected: nothing removes them when the plan goes.

## Consequences

- A comment costs a sentence instead of a tag.
- A genuine false positive goes into the test's `_ALLOWED` table with its reason.

## Verification

`tests/unit/test_no_project_history.py`.
