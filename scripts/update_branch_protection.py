#!/usr/bin/env python3
"""Show or apply `main`'s branch protection: the reviewed list of required status checks.

REQUIRED_STATUS_CHECKS is the source of truth for which CI jobs must pass before a PR merges.
Update it in the same PR that adds, renames or removes one of those jobs; then a maintainer runs
`apply` by hand (no workflow holds a token that can change protection, deliberately).

Needs the GitHub CLI (`gh`), authenticated as a repository admin.

    python3 scripts/update_branch_protection.py show
    python3 scripts/update_branch_protection.py apply
    python3 scripts/update_branch_protection.py --branch "releases/1.2-dev" apply
"""

from __future__ import annotations

import argparse
import json
import subprocess  # nosec B404  # fixed `gh api` argv only
import sys

REPO = "andras-tkcs/html-artifact-deploy"

# Job names as GitHub reports them (the job's `name:`, or its id when it has none).
REQUIRED_STATUS_CHECKS = [
    "test",
    "Test (Python 3.11)",
    "Test (Python 3.12)",
    "Test (Python 3.14)",
    "static-analysis",
]


def protection_payload() -> dict:
    return {
        "required_status_checks": {"strict": True, "contexts": REQUIRED_STATUS_CHECKS},
        "enforce_admins": False,
        "required_pull_request_reviews": {
            "required_approving_review_count": 1,
            "require_code_owner_reviews": True,
            "dismiss_stale_reviews": False,
        },
        "restrictions": None,
        "allow_force_pushes": False,
        "allow_deletions": False,
        "required_linear_history": False,  # PRs merge with a merge commit (CONTRIBUTING.md)
        "required_conversation_resolution": True,
    }


def _gh(args: list[str], stdin: str | None = None) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["gh", "api", *args], input=stdin, capture_output=True, text=True)  # nosec B603 B607


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("action", choices=["show", "apply"])
    parser.add_argument("--branch", default="main")
    args = parser.parse_args(argv)
    endpoint = f"repos/{REPO}/branches/{args.branch}/protection"

    if args.action == "show":
        print("Intended required checks:", ", ".join(REQUIRED_STATUS_CHECKS))
        result = _gh([endpoint])
        if result.returncode != 0:
            print(f"Live: none or not readable ({result.stderr.strip()})")
            return 0
        live = json.loads(result.stdout).get("required_status_checks", {}).get("contexts", [])
        print("Live required checks:    ", ", ".join(live) or "(none)")
        missing, extra = set(REQUIRED_STATUS_CHECKS) - set(live), set(live) - set(REQUIRED_STATUS_CHECKS)
        if missing or extra:
            print(f"Drift: missing {sorted(missing)}, extra {sorted(extra)} -- run `apply`")
            return 1
        return 0

    result = _gh(["-X", "PUT", endpoint, "--input", "-"], stdin=json.dumps(protection_payload()))
    if result.returncode != 0:
        print(f"error: {result.stderr.strip()}", file=sys.stderr)
        return 1
    print(f"Applied protection to {args.branch}: {', '.join(REQUIRED_STATUS_CHECKS)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
