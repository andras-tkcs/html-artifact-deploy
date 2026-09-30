"""Code and standing docs carry no project history: no phase names, plan IDs or bare issue numbers.

A comment tagged `Phase 3`, `P2` or `#41` points at a plan or review that will be deleted, and a
new reader cannot tell a phase name from a feature name or a closed issue from an open one. A
comment says why in its own words; when the why is a decision, it names the ADR; open work is cited
by the issue's full URL (ADR 0008).

Exempt: the ADRs and CHANGELOG.md (history by design), plan documents (deleted when their work
lands), recorded fixtures (third-party data), `.claude/` (the planning commands are about phases),
and this file, which quotes the shapes. Anything else that is not history goes in `_ALLOWED` with
its reason, rather than loosening a pattern.
"""

from __future__ import annotations

import re
import shutil
import subprocess  # nosec B404  # runs a fixed `git ls-files` argv only
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]

_EXEMPT_PREFIXES = ("docs/adr/", "CHANGELOG.md", "tests/fixtures/", ".claude/", "tests/unit/test_no_project_history.py")
_EXEMPT_SUFFIXES = ("-plan.md", "-plan-manual-steps.html")
_SKIP_DIRS = {".git", ".venv", "venv", "node_modules", "__pycache__", "dist", "build", "htmlcov", ".pytest_cache"}
_TEXT_SUFFIXES = {".py", ".md", ".yml", ".yaml", ".toml", ".sh", ".txt", ".cfg", ".ini", ".json", ".html", ""}

_PATTERNS = {
    "phase name": re.compile(r"\b[Pp]hase \d"),
    "phase ID": re.compile(r"\bP\d{1,2}(?:\.\d+)?\b"),
    "bare issue/PR number": re.compile(r"(?<![\w&#/])#\d{1,5}\b"),
    "section-number reference": re.compile(r"§\s?\d"),
    "version-qualified history": re.compile(r"\b(?:as of|since|until) v?\d+\.\d", re.IGNORECASE),
}

# (path, the exact matched text) -> why it is not history.
_ALLOWED: dict[tuple[str, str], str] = {}


def _tracked_files() -> list[str]:
    if shutil.which("git") and (REPO_ROOT / ".git").exists():
        result = subprocess.run(  # nosec B603 B607
            ["git", "ls-files", "--cached", "--others", "--exclude-standard"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
        return [line for line in result.stdout.splitlines() if line]
    return [
        str(path.relative_to(REPO_ROOT))
        for path in REPO_ROOT.rglob("*")
        if path.is_file() and not _SKIP_DIRS.intersection(path.relative_to(REPO_ROOT).parts)
    ]


def _scanned_files() -> list[str]:
    return [
        rel
        for rel in _tracked_files()
        if not rel.startswith(_EXEMPT_PREFIXES)
        and not rel.endswith(_EXEMPT_SUFFIXES)
        and Path(rel).suffix in _TEXT_SUFFIXES
        and (REPO_ROOT / rel).is_file()
    ]


def test_no_project_history() -> None:
    hits: list[str] = []
    for rel in _scanned_files():
        text = (REPO_ROOT / rel).read_text(encoding="utf-8", errors="replace")
        for lineno, line in enumerate(text.splitlines(), start=1):
            for label, pattern in _PATTERNS.items():
                for match in pattern.finditer(line):
                    if (rel, match.group(0)) not in _ALLOWED:
                        hits.append(f"{rel}:{lineno}: {label} {match.group(0)!r}: {line.strip()}")
    assert not hits, "Project history found; say why in words or name the ADR:\n" + "\n".join(hits)
