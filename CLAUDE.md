# CLAUDE.md

Notes for Claude Code sessions on HTML Artifact Deploy. Everything a human contributor also needs lives
in the ordinary docs, not here (ADR 0002):

- [`CONTRIBUTING.md`](CONTRIBUTING.md) — branch naming, pull requests, `releases/*` integration
  branches, and the plan/ADR lifecycle.
- [`docs/releasing.md`](docs/releasing.md) — versioning, cutting a tag, release notes, gating, PyPI.
- [`docs/coding-and-testing-guidelines.md`](docs/coding-and-testing-guidelines.md) — code and test
  conventions, and the definition of done.
- [`docs/testing-policy.md`](docs/testing-policy.md) — which test layer runs where.

Read those before changing anything they cover. This file covers only what is specific to working
with Claude Code.

## Commands and skills

- `.claude/skills/steward/SKILL.md` — which work has to be dispatched to a GitHub Actions runner
  instead of run locally, which pull requests to follow, and how to treat a red check.
- `/dod` (`.claude/commands/dod.md`) runs the definition-of-done gate and reports a pass/fail
  table. It reports; it does not fix.
- `/make-plan <prompt or issue>` (`.claude/commands/make-plan.md`, runs on Opus) researches the
  change first. For a small scope (one session's worth, such as a single bug-fix issue) it writes
  no plan: it hands back a self-contained prompt to paste into a new session, and says whether
  that session should run on Sonnet (the default) or Opus. For a large scope it writes a
  `docs/<slug>-plan.md` with an `## Implementation manifest` on its own `plan/<slug>` branch, cut
  from `main`. A `plan/` branch is never PR'd: `/implement` cuts the feature branch from it, and
  the plan's last phase deletes the plan document, so it reaches `main` only as that deletion in
  the feature PR. Delete the `plan/` branch once the feature PR merges. Steps only the user can do
  (third-party console setup, secrets, real-device checks) go only into the manifest's
  `manual_before` and `manual_after`, never between phases, and get a step-by-step HTML artifact.
- `/implement <plan URL>` (`.claude/commands/implement.md`) runs a plan with an
  `## Implementation manifest`. The orchestrator session (Sonnet) waits for the user to confirm
  `manual_before`, builds the manifest's `feature/<name>` branch out of
  `feature/<name>--<phase id>` branches, one Sonnet child session each, merged with `--no-ff` and
  a `Plan-Phase:` trailer, has one Opus session review the whole branch against the plan, and
  opens a single PR to `main` at the end, with `manual_after` as unchecked items. Phase branches
  are never PR'd on their own.
- `/cut-release <version>` (`.claude/commands/cut-release.md`) pre-flights `build.yml` and then
  dispatches `release.yml`, dry run first.
- `/qa-record <name>` (`.claude/commands/qa-record.md`) records one live upstream fixture on the
  self-hosted runner and pulls it back.

## Parallel sessions & worktrees

Several Claude Code sessions may run on this repo at once, each on its own task and branch. So
that one session's checkout state (branch switches, uncommitted edits) never interferes with
another's:

- Start new work in its own `git worktree` under `~/Coding/worktrees/`, not by switching branches
  in whichever checkout happens to be open. Name it
  `~/Coding/worktrees/html-artifact-deploy-<short-branch-slug>`.
- Don't reuse an existing worktree for an unrelated task: one worktree per active branch or task.
