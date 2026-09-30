# Releasing

How a HTML Artifact Deploy release is versioned, cut, built, gated and published. The checks a human
runs before a release are in [`release-testing.md`](release-testing.md).

## Versioning

There is no version string in the source tree and no version-bump commit. `pyproject.toml`
declares `dynamic = ["version"]`; the version is derived from the git tag by `setuptools_scm`, and
`src/html_artifact_deploy/__init__.py` reads it back through `importlib.metadata.version()` (ADR 0004).
Hand-bumped versions fail when two branches in flight both claim the next number; a tag cannot.

Versions use the PEP 440 short form: `1.2.0` (stable), `1.2.0a1` (alpha), `1.2.0b1` (beta),
`1.2.0rc1` (release candidate). Between tags, the version is a synthesized dev version
(`1.2.1.dev3+gabc1234`).

A checkout needs its full tag history for this to resolve. A shallow clone falls back to
`fallback_version` (`0.0.0.dev0`), a placeholder that is never a shipped version. Every workflow
that installs the package checks out with `fetch-depth: 0`; do the same in any new one. The Claude
Code session-start hook unshallows the web checkout for the same reason.

## Cutting a release

**A release is a tag, not a commit.** Once `main` is at the commit to release:

```
python3 scripts/tag_release.py 1.2.0          # checks, then creates v1.2.0 locally
git push origin v1.2.0
```

`scripts/tag_release.py` runs the checks the tag step otherwise has no gate for: clean tree, at
`origin/main`'s tip, no second tag on the commit, PEP 440 short form, sequential with no gaps. See
its docstring.

**Or cut it from the Actions tab.** `.github/workflows/release.yml` (`workflow_dispatch`, inputs
`version` and `dry_run`) resolves the channel, renders the stable release notes, and runs
`tag_release.py` against `main`'s tip on a runner. `dry_run` **defaults to true**: it runs every
check and creates the tag on the runner without pushing it, which is also the way to read the
release notes that would ship without shipping them. This is the only way to cut a release from a
sandboxed session (such as Claude Code on the web), which can push branches but not tags; see
`/cut-release`.

It needs one secret, `RELEASE_TAG_TOKEN`, and **it may not be the `GITHUB_TOKEN`**: GitHub starts
no workflow for an event the `GITHUB_TOKEN` raised, so a tag pushed with it would create the tag,
start no `build.yml`, and report success. Use a fine-grained personal access token with
**Contents: write** on this repository only (or a GitHub App token). The workflow refuses to start
without the secret, and after pushing it waits for a `build.yml` run on the tagged commit and
fails loudly if none appears (ADR 0006).

**Pre-flight `build.yml` before tagging.** `release.yml` builds nothing. Dispatch `build.yml`
(no inputs) against the exact commit to tag and confirm every job before the publish jobs
succeeds. It is safe on an untagged commit: every publish step is gated on a tag ref.

**One release tag per commit.** `git describe` picks one tag when a commit carries several, so a
second tag on a released commit can build under the wrong version. Move the release forward onto
a new commit instead; `tag_release.py` refuses.

## Release notes come from CHANGELOG.md

A stable release's GitHub Release body is `CHANGELOG.md`'s section for that version, rendered by
`scripts/changelog_section.py` (ADR 0005). That makes the notes a pull-request deliverable: **the
PR that prepares a release leaves `CHANGELOG.md` with exactly one `## [X.Y.Z] — YYYY-MM-DD`
heading for the version, a fresh empty `## [Unreleased]` above it, and the link definitions at the
bottom updated — before tagging.**

Usually that means renaming `## [Unreleased]`. Check first whether a section for that version
already exists: renaming on top of one produces a second heading. If one exists, merge
`[Unreleased]`'s entries into it and correct its date.

The render fails, deliberately, when the version has no section, when it has two, and when
`[Unreleased]` still has entries: each of those would otherwise ship wrong notes from a green
build. `--allow-unreleased` exists for reading a section by hand mid-cycle; no workflow passes it.

Feature branches add under `## [Unreleased]` and never open a version heading. Pre-release tags get
no section of their own; they fold into the version they lead to, and their GitHub Release entry
(marked pre-release) carries no notes.

## Build and publish

A `v*` tag push starts `.github/workflows/build.yml`:

1. **`build`** — builds the sdist and wheel.
2. **`smoke`** — installs the wheel into a clean virtual environment on Linux (ADR 0010) and runs
   the `packaged` smoke test against the installed entry point.
3. **`publish-testpypi`**, then **`publish-pypi`** — stable tags only, each gated on the one
   before, so a broken upload never reaches the real index.
4. **`github-release`** — creates the GitHub Release with the built files; for a stable tag the
   body is the changelog section, for a pre-release the entry is marked pre-release.

## Publishing to PyPI

Both indexes are published through PyPI's OIDC **Trusted Publisher** mechanism: GitHub mints a
short-lived token for the job, and there is no long-lived API token in this repository (ADR 0009).

Before the first publish, register the workflow as a pending Trusted Publisher on **both**
services (`test.pypi.org/manage/account/publishing/` and `pypi.org/manage/account/publishing/`):

- **PyPI Project Name**: `html-artifact-deploy`
- **Owner**: `andras-tkcs`
- **Repository name**: `html-artifact-deploy`
- **Workflow name**: `build.yml`
- **Environment name**: `testpypi` on TestPyPI, `pypi` on PyPI

Create the GitHub Environments `testpypi`, `pypi` and `release` (repository **Settings →
Environments**). A required reviewer on `pypi` or `release` turns publishing into a two-person
go/no-go.

`workflow_dispatch` from an untagged commit builds a dev version with a local segment (`+g<sha>`),
which both indexes reject; that is why every publish job is gated on a tag ref.
