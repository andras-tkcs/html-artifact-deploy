# ADR 0009: PyPI publishing uses OIDC trusted publishing only

## Status

Accepted — 2026-09-30. Inherited from the MCP server template.

## Context

Publishing to PyPI needs a credential. A long-lived API token stored as a repository secret can
be exfiltrated by any workflow step that reads it, and outlives whoever created it.

## Decision

`build.yml` publishes to TestPyPI and then PyPI through PyPI's Trusted Publisher mechanism
(`pypa/gh-action-pypi-publish` with `id-token: write`), scoped to the `testpypi` and `pypi`
GitHub Environments. There is no PyPI token in the repository. TestPyPI goes first, and PyPI
depends on it.

## Alternatives considered

- **An API token secret.** Rejected for the reasons above.

## Consequences

- A one-time registration of the pending publisher on both indexes (`docs/releasing.md`).
- A required reviewer on the `pypi` environment is available as a go/no-go gate.

## Verification

`.github/workflows/build.yml`'s publish jobs (`permissions`, `environment`).
