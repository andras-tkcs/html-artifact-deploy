#!/usr/bin/env python3
"""Check the real upstream service against committed fixtures, or record new ones.

Layer 5 of docs/testing-policy.md. Unit tests replay redacted fixtures from
`tests/fixtures/live/<name>/` and never touch the network; this script is what keeps those fixtures
honest. It needs real credentials for a dedicated QA account, which exist only on the self-hosted
runner and on a maintainer's QA machine, never on a GitHub-hosted runner (ADR 0007).

    python3 scripts/live_check.py --check            # every registered check; never writes
    python3 scripts/live_check.py --check <name>     # one check
    python3 scripts/live_check.py --record <name>    # fetch, redact, write the fixture

`--check` fetches the live response, redacts it and compares its *shape* (keys and value types,
not values) with the committed fixture, and reports each fixture's age: under 60 days healthy,
60-90 a warning, over 90 a refresh is required. It exits 1 on drift, on a missing fixture, and on
a name that is not registered: a name with no check must never pass green having checked nothing.

Register a check by adding a `LiveCheck` to CHECKS. `fetch` gets the live data using credentials
from the environment; `redact` replaces every account identifier, e-mail, tenant URL and token
with a placeholder. After `--record`, read the diff: a real identifier in it means `redact` needs
fixing before anything is committed.
"""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_ROOT = REPO_ROOT / "tests" / "fixtures" / "live"
WARN_DAYS = 60
STALE_DAYS = 90


@dataclass(frozen=True)
class LiveCheck:
    fixture: str  # file name under tests/fixtures/live/<check name>/
    fetch: Callable[[], Any]
    redact: Callable[[Any], Any]


# name -> check. Empty until the server talks to an upstream service.
CHECKS: dict[str, LiveCheck] = {}


def shape(value: Any) -> Any:
    """The structure of a JSON value with every leaf replaced by its type name."""
    if isinstance(value, dict):
        return {key: shape(item) for key, item in sorted(value.items())}
    if isinstance(value, list):
        return [shape(value[0])] if value else []
    return type(value).__name__


def fixture_path(name: str) -> Path:
    return FIXTURE_ROOT / name / CHECKS[name].fixture


def run_check(name: str) -> bool:
    path = fixture_path(name)
    if not path.is_file():
        print(f"FAIL {name}: no fixture at {path.relative_to(REPO_ROOT)} -- record one first")
        return False
    age_days = (time.time() - path.stat().st_mtime) / 86400
    committed = json.loads(path.read_text(encoding="utf-8"))
    live = CHECKS[name].redact(CHECKS[name].fetch())
    if shape(live) != shape(committed):
        print(f"FAIL {name}: the live response's shape differs from the fixture (provider drift)")
        return False
    note = "healthy" if age_days < WARN_DAYS else "refresh soon" if age_days < STALE_DAYS else "REFRESH REQUIRED"
    print(f"ok   {name}: shape matches; fixture is {age_days:.0f} days old ({note})")
    return age_days < STALE_DAYS


def record(name: str) -> None:
    path = fixture_path(name)
    path.parent.mkdir(parents=True, exist_ok=True)
    data = CHECKS[name].redact(CHECKS[name].fetch())
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    print(f"recorded {path.relative_to(REPO_ROOT)} -- read the diff before committing it")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", nargs="*", metavar="NAME", help="check these (default: all)")
    mode.add_argument("--record", metavar="NAME", help="record one fixture")
    args = parser.parse_args(argv)

    names = [args.record] if args.record else (args.check or sorted(CHECKS))
    unknown = [name for name in names if name not in CHECKS]
    if unknown:
        print(f"error: not registered in CHECKS: {', '.join(unknown)}", file=sys.stderr)
        return 1
    if not names:
        print("No live checks registered yet.")
        return 0
    if args.record:
        record(args.record)
        return 0
    results = [run_check(name) for name in names]
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
