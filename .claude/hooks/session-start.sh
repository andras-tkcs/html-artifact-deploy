#!/bin/bash
# SessionStart hook for Claude Code on the web: make a fresh container able to run the definition
# of done (docs/coding-and-testing-guidelines.md) without a manual setup step first.
#
# Two problems, in order of how quietly they break:
#
# 1. The web checkout arrives shallow and without tags. setuptools_scm resolves the version through
#    `git describe`; with no tags it falls back to fallback_version, and `pip install -e .` bakes
#    that placeholder into the installed metadata, so every later version read -- and
#    scripts/tag_release.py's sequence check -- is wrong without saying so (ADR 0004). So this
#    fetches the full history and tags first.
#
# 2. Nothing is installed: no venv, the package isn't importable, ruff/bandit/mypy aren't there.
#
# Synchronous on purpose (no {"async": true}): a session that starts before the tag fetch finishes
# can install the fallback version, which is the failure this exists to prevent.
set -euo pipefail

# Web sessions only. A local checkout is the developer's own, possibly one of several worktrees,
# and this must not reach into it.
if [ "${CLAUDE_CODE_REMOTE:-}" != "true" ]; then
  exit 0
fi

cd "${CLAUDE_PROJECT_DIR:-$(git rev-parse --show-toplevel)}"

echo "==> Restoring full history and tags (setuptools_scm needs them)"
if [ "$(git rev-parse --is-shallow-repository)" = "true" ]; then
  # --unshallow fails on a complete repo, hence the branch; the plain --tags retry covers a server
  # that refuses the deepening but still hands over tags.
  git fetch --unshallow --tags origin || git fetch --tags origin
else
  git fetch --tags origin || true
fi

if git describe --tags >/dev/null 2>&1; then
  echo "    version resolves as: $(git describe --tags)"
else
  # Normal before the first release; after it, this means the fetch above failed.
  echo "    note: no tag reachable from HEAD; the version will be fallback_version (fine before the first release)"
fi

echo "==> Installing the package with the dev extra into .venv"
# A venv with its own current pip/setuptools, rather than the container's system Python, whose
# distribution-patched tooling can fail to build some sdists.
if [ ! -x .venv/bin/python ]; then
  python3 -m venv --upgrade-deps .venv
fi
.venv/bin/python -m pip install --quiet -e ".[dev]"

# Put the venv first on PATH for the rest of the session, so `pytest`, `ruff`, `bandit` and `mypy`
# mean the ones just installed, not copies elsewhere on the container's PATH.
if [ -n "${CLAUDE_ENV_FILE:-}" ]; then
  echo "export PATH=\"$PWD/.venv/bin:\$PATH\"" >> "$CLAUDE_ENV_FILE"
else
  echo "    WARNING: no CLAUDE_ENV_FILE; run tools as .venv/bin/<tool>"
fi

echo "==> Ready. See .claude/skills/steward/SKILL.md for what to run off-box."
