#!/usr/bin/env python3
"""Coverage ratchet: fail if coverage drops below its recorded floor.

`pytest --cov-report=term-missing` only reports coverage. This script is the gate: it reads the
`coverage.json` that `pytest --cov-report=json:coverage.json` writes and fails if

  * overall branch+line coverage (`totals.percent_covered`, the number pytest-cov's summary line
    prints) is below OVERALL_FLOOR, or
  * any module in MODULE_FLOORS is below its own, individually higher, floor.

MODULE_FLOORS exists because one overall number protects no single file: a security-critical
module that is 2% of the package can lose half its coverage inside the aggregate. List the modules
whose failure would matter most (auth, input validation, anything that decides what data leaves
the server) with a comment saying why each one is there.

A floor only moves up. A PR whose tests raise a module's coverage raises its floor in the same PR;
a PR that needs to lower one is a coverage regression and says so in its description, never a
silent edit here. A new floor is set to the measured value rounded down to a whole percent, as
headroom against float jitter.

    python3 scripts/check_coverage_floor.py coverage.json
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

OVERALL_FLOOR = 100.0

MODULE_FLOORS: dict[str, float] = {
    # The tool surface every MCP client sees.
    "src/html_artifact_deploy/server.py": 100.0,
    # The configuration's fail-closed checks.
    "src/html_artifact_deploy/config.py": 100.0,
    # Writes into the published folder.
    "src/html_artifact_deploy/storage.py": 100.0,
    # Stores and checks every bearer token and sign-in secret.
    "src/html_artifact_deploy/token_store.py": 100.0,
    # Decides who may sign in and which redirect URIs receive a code.
    "src/html_artifact_deploy/oauth_provider.py": 100.0,
    # Wires authentication, host checks and body limits around the tools.
    "src/html_artifact_deploy/http_app.py": 100.0,
    # Accepts file bodies from callers who hold only a one-time URL.
    "src/html_artifact_deploy/uploads.py": 100.0,
}


def _load(cov_json: dict) -> tuple[float, dict[str, float]]:
    overall = cov_json["totals"]["percent_covered"]
    per_file = {path: data["summary"]["percent_covered"] for path, data in cov_json["files"].items()}
    return overall, per_file


def check(cov_json: dict) -> list[str]:
    """Every floor that is not met, as a human-readable line. Empty means the gate passes."""
    overall, per_file = _load(cov_json)
    failures: list[str] = []
    print(f"Overall coverage: {overall:.2f}% (floor {OVERALL_FLOOR:.1f}%)")
    if overall < OVERALL_FLOOR:
        failures.append(f"overall coverage {overall:.2f}% is below the {OVERALL_FLOOR:.1f}% floor")
    if MODULE_FLOORS:
        print("\nModule floors:")
    for path, floor in MODULE_FLOORS.items():
        if path not in per_file:
            # A module that vanished from the report was renamed or deleted without updating this
            # table, or is no longer imported by any test. Either way the floor protects nothing.
            failures.append(f"{path} is not in the coverage report (renamed, deleted, or never imported?)")
            print(f"  MISSING {path}")
            continue
        actual = per_file[path]
        status = "ok  " if actual >= floor else "FAIL"
        print(f"  {status} {path}: {actual:.2f}% (floor {floor:.1f}%)")
        if actual < floor:
            failures.append(f"{path} coverage {actual:.2f}% is below its {floor:.1f}% floor")
    return failures


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("coverage_json", nargs="?", default="coverage.json")
    args = parser.parse_args(argv)

    path = Path(args.coverage_json)
    if not path.is_file():
        print(
            f"error: {path} not found -- run pytest with --cov=src/html_artifact_deploy --cov-branch "
            "--cov-report=json:coverage.json first",
            file=sys.stderr,
        )
        return 2

    failures = check(json.loads(path.read_text(encoding="utf-8")))
    if failures:
        print("\nCoverage floor FAILED:", file=sys.stderr)
        for line in failures:
            print(f"  - {line}", file=sys.stderr)
        print("\nAdd tests for the new code. Lowering a floor is a regression, not a config edit.", file=sys.stderr)
        return 1
    print("\nCoverage floor passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
