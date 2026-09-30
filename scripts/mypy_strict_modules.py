#!/usr/bin/env python3
"""Run mypy, blocking, over every module pyproject.toml has promoted to strict.

The whole-tree `mypy src/html_artifact_deploy` run is informational. The ratchet is this: every
`[[tool.mypy.overrides]]` block that turns on a strictness flag promotes its module, and CI fails
if a promoted module regresses. The list is read from pyproject.toml, so promoting the next module
is a one-block edit there with nothing to change in a workflow.

Wildcard module patterns are refused: a promotion is a per-module decision, and
`module = "html_artifact_deploy.*"` would widen the blocking set every time a file landed.

    python3 scripts/mypy_strict_modules.py          # run mypy over the promoted modules
    python3 scripts/mypy_strict_modules.py --list   # print what would be checked
"""

from __future__ import annotations

import argparse
import subprocess  # nosec B404  # runs a fixed mypy argv only
import sys
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_PYPROJECT = REPO_ROOT / "pyproject.toml"
SRC_ROOT = REPO_ROOT / "src"

STRICTNESS_FLAGS = frozenset(
    {
        "disallow_untyped_defs",
        "disallow_incomplete_defs",
        "disallow_untyped_calls",
        "disallow_any_generics",
        "disallow_any_explicit",
        "disallow_untyped_decorators",
        "warn_return_any",
        "warn_unreachable",
        "no_implicit_reexport",
        "strict_equality",
        "strict",
    }
)


class ResolutionError(Exception):
    """A promoted module name that does not correspond to a file under src/."""


def promoted_modules(pyproject: Path = DEFAULT_PYPROJECT) -> list[str]:
    data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    names: list[str] = []
    for override in data.get("tool", {}).get("mypy", {}).get("overrides", []):
        if not any(override.get(flag) is True for flag in STRICTNESS_FLAGS):
            continue
        module = override.get("module")
        for name in [module] if isinstance(module, str) else list(module or []):
            if name not in names:
                names.append(name)
    return names


def module_path(module: str, src_root: Path = SRC_ROOT) -> Path:
    if "*" in module:
        raise ResolutionError(f"{module!r}: wildcards are not supported -- promote modules one at a time")
    parts = module.split(".")
    for candidate in (src_root.joinpath(*parts).with_suffix(".py"), src_root.joinpath(*parts, "__init__.py")):
        if candidate.is_file():
            return candidate
    raise ResolutionError(f"{module!r}: no such module under {src_root} -- renamed without updating pyproject.toml?")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--list", action="store_true", help="print the resolved paths instead of running mypy")
    args = parser.parse_args(argv)
    try:
        paths = [module_path(name) for name in promoted_modules()]
    except ResolutionError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2
    if not paths:
        print("No modules promoted yet; nothing to check.")
        return 0
    if args.list:
        for path in paths:
            print(path.relative_to(REPO_ROOT))
        return 0
    # --follow-imports=silent: check the promoted files strictly without reporting errors in the
    # unpromoted modules they import, which the informational whole-tree run already shows.
    cmd = [sys.executable, "-m", "mypy", "--follow-imports=silent", *map(str, paths)]
    return subprocess.run(cmd, cwd=REPO_ROOT).returncode  # nosec B603


if __name__ == "__main__":
    sys.exit(main())
