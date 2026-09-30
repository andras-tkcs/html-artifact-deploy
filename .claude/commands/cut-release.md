---
description: Pre-flight and cut a release tag through the release workflow
argument-hint: "<version>  e.g. 1.2.0, or 1.2.0a1 / 1.2.0b2 / 1.2.0rc1"
---

Cut the release: **$ARGUMENTS**

If no version was given above, stop and ask. Never infer the next version from `CHANGELOG.md` —
the git tag, through `setuptools_scm`, is the only version source (ADR 0004).

A cloud container cannot push `refs/tags/*`, so the tag is cut by `.github/workflows/release.yml`.
Do not try to `git tag && git push` from here; it will fail on the push and may leave a local tag
behind that makes later checks lie.

**Before dispatching anything**, report the state so I can sanity-check it:

- `git fetch origin main --tags`, and what commit `origin/main` is at
  (`git log -1 --oneline origin/main`). That commit is what gets tagged.
- `python3 scripts/release_channel.py <version>` — the channel this version resolves to.
- `git tag --list 'v*' --sort=-v:refname | head` — the most recent tags, so the sequence is
  visible. A pre-release must be exactly +1 on its stage; a stable must be an unskipped bump.
- For a **stable** version only: `python3 scripts/changelog_section.py <version>` must succeed;
  show me the rendered notes, which are the literal GitHub Release body. Never pass
  `--allow-unreleased`. If it fails because `[Unreleased]` is still populated, the fix is a PR that
  merges those entries into the version's section, not a flag.

**Then pre-flight `build.yml` against that same commit — do not skip this** (ADR 0006).
`release.yml` builds nothing, so it cannot catch a regression in the packaged smoke test. Dispatch
`.github/workflows/build.yml` (`workflow_dispatch`, no inputs) against `origin/main`'s exact commit
and wait for `build` and every `smoke` leg to finish. Every publish job is gated on a tag ref, so
this run publishes nothing. Report the run URL and its outcome.

If any of them fail: stop. Treat it as an ordinary CI failure on `main` — diagnose, fix, push, and
re-run this pre-flight against the fixed commit — before touching `release.yml`.

**Only once the pre-flight is green, dispatch `release.yml` against `main` with
`dry_run: true`** and report the result. The dry run performs every check and creates the tag on
the runner without pushing it.

**Only cut for real once I have seen the dry run and said to.** Then re-dispatch with
`dry_run: false`, and watch it: the workflow verifies that `build.yml` started for the tagged
commit, and a failure there means the tag exists but the release did not start — tell me
immediately if that happens; it needs the tag deleted and re-pushed, not a retry.

After a real cut, report the tag and the `build.yml` run URL, and follow that run to the end:
report which publish jobs ran and whether the GitHub Release was created.
