#!/usr/bin/env python3
"""Print the release channel a version belongs to: stable, alpha, beta or rc.

Versions use the PEP 440 short form (docs/releasing.md): `1.2.0` is stable, `1.2.0a1` alpha,
`1.2.0b1` beta, `1.2.0rc1` a release candidate. Only a stable version reaches PyPI and gets release
notes from CHANGELOG.md; the workflows ask this script rather than carrying their own copy of the
parsing rule.

    python3 scripts/release_channel.py 1.2.0rc1   # -> rc
"""

from __future__ import annotations

import re
import sys

_VERSION_RE = re.compile(r"^v?\d+\.\d+\.\d+(?:(a|b|rc)\d+)?$")
_CHANNELS = {None: "stable", "a": "alpha", "b": "beta", "rc": "rc"}


def channel(version: str) -> str:
    match = _VERSION_RE.match(version.strip())
    if not match:
        raise ValueError(f"{version!r} is not a release version (expected X.Y.Z, optionally aN/bN/rcN)")
    return _CHANNELS[match.group(1)]


def main(argv: list[str] | None = None) -> int:
    args = sys.argv[1:] if argv is None else argv
    if len(args) != 1:
        print("usage: release_channel.py <version>", file=sys.stderr)
        return 2
    try:
        print(channel(args[0]))
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
