# ADR 0004: The version comes from the git tag; no version string in the source tree

## Status

Accepted — 2026-09-30. Inherited from the MCP server template.

## Context

A version kept in files and bumped by a commit fails in a predictable way: two branches in flight
both bump to the same next version, one merges after another release already took that number,
and a release ships with the wrong version or has to be reverted.

## Decision

`pyproject.toml` declares `dynamic = ["version"]` and `setuptools_scm` derives the version from the
git tag. The package reads it back with `importlib.metadata.version()`. There is no version string
in the source tree, and no version-bump commit. `CHANGELOG.md` is never parsed to decide a version.

## Alternatives considered

- **A `__version__` constant bumped by the release PR.** Rejected for the failure above.
- **A version file written by CI.** Rejected: a second source of truth that can disagree with the
  tag.

## Consequences

- A checkout without tags resolves to `fallback_version` (`0.0.0.dev0`). Every workflow checks out
  with `fetch-depth: 0`, and the Claude Code web session hook unshallows its checkout.
- A commit with two release tags builds as whichever `git describe` prefers, so
  `scripts/tag_release.py` refuses a second tag on a commit (ADR 0006).

## Verification

`pyproject.toml` (`dynamic`, `[tool.setuptools_scm]`); `src/html_artifact_deploy/__init__.py`;
`fetch-depth: 0` in `.github/workflows/*.yml`; `.claude/hooks/session-start.sh`.
