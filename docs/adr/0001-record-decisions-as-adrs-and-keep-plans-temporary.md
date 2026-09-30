# ADR 0001: Record decisions as ADRs; plans are temporary and deleted when their work lands

## Status

Accepted — 2026-09-30. Inherited from the MCP server template.

## Context

A project accumulates three kinds of writing: plans (what we are about to do), reference docs
(how it works today) and the reasons behind decisions (why it is this way, and what was
rejected). When all three live in the same documents, plans linger long after their work landed
and describe a design the code no longer has, reference docs fill with "previously" narratives,
and the rejected alternatives — the part a newcomer most needs — are lost when the plan is
eventually cleaned up.

Work here is often planned and implemented by AI coding sessions (`/make-plan`, `/implement`),
which produce plan documents quickly. Without a rule for retiring them, they would pile up.

## Decision

1. Three kinds of document with three lifecycles, as `docs/adr/README.md` describes: plans are
   temporary, ADRs are permanent and frozen once accepted, reference docs describe today.
2. A plan document is deleted in the PR that completes its work, and that PR adds or amends an ADR
   for every decision the plan made that meets the ADR bar, or says it made none.
3. ADRs link only to things that last: issues, PRs, commits, source files, other ADRs.

## Alternatives considered

- **Keep plans as permanent design documents.** Rejected: they go stale the day the code
  diverges, and nothing marks which parts still hold.
- **Put the reasoning in reference docs.** Rejected: reference docs are edited with every change,
  and a rejected alternative has no behavior to keep it there.

## Consequences

- Retiring a plan is part of the definition of done, and `/make-plan`'s last phase always
  includes it.
- An accepted ADR's body is never rewritten; changing a decision costs a new ADR.

## Verification

`docs/adr/README.md` (rules and index); the definition of done in
`docs/coding-and-testing-guidelines.md`; `.claude/commands/make-plan.md`'s retirement phase.
