#!/usr/bin/env python3
"""Create a release tag after the checks a human tagging by hand keeps missing.

Cutting a release is "tag `main`'s tip and push the tag" (docs/releasing.md). There is no
version-bump commit and no build step before the tag to catch a mistake, so this script runs the
checks that step has no other gate for, before creating the tag locally:

  1. **Top of main.** The checkout is on `main`, the tree is clean, and local `main` equals
     `origin/main` after a fetch. HEAD must not already carry a release tag: `git describe`, which
     setuptools_scm resolves the version from, picks one tag when a commit has several, so a second
     tag can build and publish under the wrong version (ADR 0006).
  2. **Version format.** `vMAJOR.MINOR.PATCH`, optionally `a<N>`/`b<N>`/`rc<N>` (PEP 440 short
     form). Existing tags in other spellings (`-beta1`) are read, but never written.
  3. **Sequential, no gaps.** A pre-release number is exactly one more than the highest existing
     one of the same stage for that version (or 1). A stable version is a patch, minor or major
     bump of the latest stable, unless a pre-release already led up to it.

It does not decide *what* to release, and it does not check CHANGELOG.md
(`scripts/changelog_section.py` does that).

It only creates the tag locally unless `--push` is given: pushing starts `build.yml`, which
publishes.

Stdlib only; carries its own copy of the version regexes on purpose, so it does not depend on
anything else in scripts/.

Examples:
    python3 scripts/tag_release.py 1.2.0a1          # checks, then creates v1.2.0a1 locally
    python3 scripts/tag_release.py 1.2.0 --push     # checks, creates and pushes v1.2.0
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path
from typing import NamedTuple

REPO_ROOT = Path(__file__).resolve().parent.parent

# The scheme new tags must use: PEP 440 short form, a=alpha, b=beta, rc=release candidate.
_VERSION_RE = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:(a|b|rc)(\d+))?$")

# What an *existing* tag may look like: more permissive than _VERSION_RE, so tags spelled "-alpha1",
# "-beta2" or "-preview3" still count toward the sequential/no-gaps check.
_TAG_RE = re.compile(
    r"^v?(\d+)\.(\d+)\.(\d+)(?:[-_.]?(alpha|beta|preview|pre|rc|c|a|b)[-_.]?(\d+))?(?:[-_.]?dev(\d+))?(?:\+.*)?$",
    re.IGNORECASE,
)
_TAG_STAGE_ALIASES = {
    "alpha": "a",
    "a": "a",
    "beta": "b",
    "b": "b",
    "preview": "rc",
    "pre": "rc",
    "c": "rc",
    "rc": "rc",
}


class TagError(Exception):
    """One of this script's checks failed. Caught in main() and printed without a traceback."""


class Identity(NamedTuple):
    """The (major, minor, patch, stage, num) a tag or version string names. `stage` is `""` and
    `num` is `0` for a stable release -- there is no pre-release number to compare."""

    major: int
    minor: int
    patch: int
    stage: str
    num: int

    @property
    def line(self) -> tuple[int, int, int]:
        return (self.major, self.minor, self.patch)


def parse_version(version: str) -> Identity:
    """Parses a version *to be tagged* -- strict, canonical short form only. Raises TagError."""
    match = _VERSION_RE.match(version.strip())
    if not match:
        raise TagError(
            f"{version!r} isn't a valid release version -- expected major.minor.patch, optionally "
            "followed by a pre-release suffix a<N>/b<N>/rc<N> (PEP 440 short form; see "
            "docs/releasing.md). Spellings like '-beta1' are read in existing tags but must not "
            "be used for a new one."
        )
    major, minor, patch, stage, num = match.groups()
    return Identity(int(major), int(minor), int(patch), stage or "", int(num) if num else 0)


def _parse_existing_tag(tag: str) -> Identity | None:
    """Parses a tag already in the repo, tolerating other spellings. Returns None for
    anything that isn't a release tag at all (a non-release tag, or a malformed one) -- those simply
    don't participate in the sequential/no-gaps check."""
    match = _TAG_RE.match(tag.strip())
    if not match:
        return None
    major, minor, patch, stage, num, dev = match.groups()
    if dev is not None:
        return None  # a real pushed tag is never a synthesized dev version
    stage_short = _TAG_STAGE_ALIASES[stage.lower()] if stage else ""
    return Identity(int(major), int(minor), int(patch), stage_short, int(num) if num else 0)


def tag_name(identity: Identity) -> str:
    suffix = f"{identity.stage}{identity.num}" if identity.stage else ""
    return f"v{identity.major}.{identity.minor}.{identity.patch}{suffix}"


def next_version_options(latest_stable: tuple[int, int, int]) -> set[tuple[int, int, int]]:
    """The only `major.minor.patch` triples that don't skip a version after `latest_stable`."""
    major, minor, patch = latest_stable
    return {(major, minor, patch + 1), (major, minor + 1, 0), (major + 1, 0, 0)}


def _raise_not_next(line: tuple[int, int, int], latest_stable: tuple[int, int, int]) -> None:
    options = ", ".join(".".join(map(str, option)) for option in sorted(next_version_options(latest_stable)))
    raise TagError(
        f"{'.'.join(map(str, line))} isn't a valid next version after the latest stable release "
        f"v{'.'.join(map(str, latest_stable))} -- expected one of: {options} (a patch, minor, or "
        "major bump; no version may be skipped)."
    )


def check_sequential(identity: Identity, known: list[Identity]) -> None:
    """Raises TagError unless `identity` continues this repo's release numbering with no gap.
    `known` is every already-tagged release identity (see known_identities()) -- pure and
    git-independent so it's cheap to test against a fabricated history."""
    same_line = [existing for existing in known if existing.line == identity.line]
    stable_identities = [existing for existing in known if not existing.stage]
    latest_stable = max((existing.line for existing in stable_identities), default=None)

    if identity.stage:
        already_stable = [existing for existing in same_line if not existing.stage]
        if already_stable:
            raise TagError(
                f"{'.'.join(map(str, identity.line))} was already released as a stable version -- "
                "a new pre-release needs a higher version."
            )

        stage_siblings = [existing for existing in same_line if existing.stage == identity.stage]
        expected_num = max((existing.num for existing in stage_siblings), default=0) + 1
        if identity.num != expected_num:
            existing_names = (
                ", ".join(tag_name(existing) for existing in sorted(stage_siblings, key=lambda existing: existing.num))
                or "(none yet)"
            )
            raise TagError(
                f"next {identity.stage}-release for {'.'.join(map(str, identity.line))} must be "
                f"{identity.stage}{expected_num} (got {identity.stage}{identity.num}). Existing "
                f"{identity.stage}-releases for this version: {existing_names}"
            )

        if not same_line and latest_stable is not None and identity.line not in next_version_options(latest_stable):
            _raise_not_next(identity.line, latest_stable)
    else:
        already_stable = [existing for existing in same_line if not existing.stage]
        if already_stable:
            raise TagError(
                f"v{'.'.join(map(str, identity.line))} is already tagged as a stable release "
                f"({', '.join(tag_name(existing) for existing in already_stable)})"
            )

        # A pre-release already targeting this exact version already passed this check when *it*
        # was tagged -- finalizing it to stable isn't a new jump in the version line.
        if not same_line and latest_stable is not None and identity.line not in next_version_options(latest_stable):
            _raise_not_next(identity.line, latest_stable)


def _git(args: list[str], *, cwd: Path) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if result.returncode != 0:
        raise TagError(f"git {' '.join(args)} failed:\n{result.stderr.strip()}")
    return result.stdout.strip()


def check_top_of_main(cwd: Path, *, remote: str = "origin", branch: str = "main", fetch: bool = True) -> None:
    current = _git(["rev-parse", "--abbrev-ref", "HEAD"], cwd=cwd)
    if current != branch:
        raise TagError(f"checked-out branch is {current!r}, not {branch!r} -- switch to {branch} before tagging")

    status = _git(["status", "--porcelain"], cwd=cwd)
    if status:
        raise TagError("working tree isn't clean -- commit, stash, or discard changes before tagging")

    if fetch:
        _git(["fetch", remote, branch], cwd=cwd)

    head = _git(["rev-parse", "HEAD"], cwd=cwd)
    try:
        remote_head = _git(["rev-parse", f"{remote}/{branch}"], cwd=cwd)
    except TagError as exc:
        raise TagError(f"can't resolve {remote}/{branch}: {exc}") from exc

    if head != remote_head:
        raise TagError(
            f"local {branch} ({head[:12]}) is not {remote}/{branch} ({remote_head[:12]}) -- "
            f"push or pull so local {branch} matches {remote} before tagging."
        )


def check_no_tag_at_head(cwd: Path) -> None:
    head = _git(["rev-parse", "HEAD"], cwd=cwd)
    existing = _git(["tag", "--points-at", head], cwd=cwd)
    tags = [line for line in existing.splitlines() if line.strip()]
    if tags:
        raise TagError(
            f"HEAD already carries tag(s) {', '.join(tags)} -- `git describe` (which setuptools_scm "
            "resolves __version__ from) picks one tag when a commit carries several, so a second "
            "tag here can silently build under the wrong version (ADR 0006). Move the release "
            "forward onto a new commit instead."
        )


def known_identities(cwd: Path) -> list[Identity]:
    output = _git(["tag", "--list"], cwd=cwd)
    identities = []
    for line in output.splitlines():
        parsed = _parse_existing_tag(line)
        if parsed is not None:
            identities.append(parsed)
    return identities


def create_tag(cwd: Path, name: str) -> None:
    _git(["tag", name], cwd=cwd)


def push_tag(cwd: Path, remote: str, name: str) -> None:
    _git(["push", remote, name], cwd=cwd)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__.split("\n\n")[0], formatter_class=argparse.RawDescriptionHelpFormatter
    )
    parser.add_argument("version", help="Version to tag, e.g. 1.2.0 or 1.2.0a1 (a leading 'v' is tolerated)")
    parser.add_argument("--remote", default="origin", help="Remote to check main against and push to (default: origin)")
    parser.add_argument("--branch", default="main", help="Branch a release is cut from (default: main)")
    parser.add_argument("--repo", type=Path, default=REPO_ROOT, help="Path to the git checkout (default: this repo)")
    parser.add_argument(
        "--no-fetch",
        action="store_true",
        help="Skip 'git fetch' before checking that main is up to date -- only if you already fetched",
    )
    parser.add_argument(
        "--push",
        action="store_true",
        help="Also push the tag once created -- starts build.yml immediately",
    )
    args = parser.parse_args(argv)

    try:
        identity = parse_version(args.version)
        name = tag_name(identity)

        check_top_of_main(args.repo, remote=args.remote, branch=args.branch, fetch=not args.no_fetch)
        check_no_tag_at_head(args.repo)

        if _git(["tag", "--list", name], cwd=args.repo):
            raise TagError(f"{name} already exists")

        check_sequential(identity, known_identities(args.repo))

        create_tag(args.repo, name)
        head = _git(["rev-parse", "--short", "HEAD"], cwd=args.repo)
        print(f"created tag {name} at {head}")

        if args.push:
            push_tag(args.repo, args.remote, name)
            print(f"pushed {name} to {args.remote} -- build.yml is now running")
        else:
            print(f"not pushed. Review, then run:\n  git push {args.remote} {name}")
    except TagError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
