# ADR 0010: The server runs on headless Linux only

## Status

Accepted — 2026-09-30.

## Context

HTML Artifact Deploy is deployed as a headless server process on Linux: no desktop, no GUI app,
no Windows or macOS install. The template it started from tests on Windows and macOS as well,
which costs runner minutes (macOS especially), slows every PR, and turns up failures that only
exist on platforms nobody runs the server on.

## Decision

1. Linux is the only supported operating system. `pyproject.toml` declares
   `Operating System :: POSIX :: Linux`.
2. `tests.yml` runs the suite on Linux only: the coverage job and one job per other supported
   Python version. There is no Windows or macOS job.
3. `build.yml`'s packaged smoke test runs on Linux only.

## Alternatives considered

- **Keep the Windows and macOS jobs as non-required checks.** Rejected: a red check nobody has to
  fix is noise, and it still costs minutes on every PR.
- **Keep only the packaged smoke test cross-platform.** Rejected: it would guard installs that are
  not supported.

## Consequences

- Platform-specific code (POSIX paths, signals, `fork`) is acceptable without a Windows
  fallback.
- Supporting another OS later means a new ADR superseding this one, and adding the jobs back.
- Code should still pass explicit encodings to file I/O: it keeps behavior independent of the
  host's locale.

## Verification

`.github/workflows/tests.yml` and `.github/workflows/build.yml` (`runs-on` of every job);
`pyproject.toml` classifiers; `scripts/update_branch_protection.py` lists no platform checks.
