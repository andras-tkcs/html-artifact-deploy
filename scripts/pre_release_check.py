#!/usr/bin/env python3
"""Run every blocking check CI runs on a pull request, and report PASS/FAIL for each.

This is the automated half of the definition of done (docs/coding-and-testing-guidelines.md,
"Definition of done") and the second gate of docs/release-testing.md. It checks no built artifact
and replaces none of the manual items. `/dod` runs the same commands and also reads the diff.

    .venv/bin/python scripts/pre_release_check.py

Every check runs even when an earlier one fails, so one run shows everything that is red.
"""

from __future__ import annotations

import subprocess  # nosec B404  # fixed argv lists only
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PY = sys.executable

CHECKS: dict[str, list[str]] = {
    "pytest": [
        PY,
        "-m",
        "pytest",
        "-v",
        "--cov=src/html_artifact_deploy",
        "--cov-branch",
        "--cov-report=term-missing",
        "--cov-report=json:coverage.json",
    ],
    "coverage floor": [PY, "scripts/check_coverage_floor.py", "coverage.json"],
    "ruff check": [PY, "-m", "ruff", "check", "."],
    "ruff format": [PY, "-m", "ruff", "format", "--check", "."],
    "mypy (promoted modules)": [PY, "scripts/mypy_strict_modules.py"],
    "bandit": [PY, "-m", "bandit", "-q", "-c", "pyproject.toml", "-r", "src"],
}


def run(name: str, cmd: list[str]) -> bool:
    print(f"--- {name} ({' '.join(cmd)}) ---", flush=True)
    try:
        ok = subprocess.run(cmd, cwd=REPO_ROOT).returncode == 0  # nosec B603
    except FileNotFoundError:
        print(f"error: {cmd[0]!r} not found -- was the venv installed with the [dev] extra?")
        ok = False
    print(f"--- {name}: {'PASS' if ok else 'FAIL'} ---\n", flush=True)
    return ok


def main() -> int:
    results = {name: run(name, cmd) for name, cmd in CHECKS.items()}
    print("=== Summary ===")
    for name, ok in results.items():
        print(f"  [{'PASS' if ok else 'FAIL'}] {name}")
    if not all(results.values()):
        return 1
    print("\nAll automated checks passed. The manual items of the definition of done still apply.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
