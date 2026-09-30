"""Command-line entry point: `python -m html_artifact_deploy` or the `html-artifact-deploy` script."""

from __future__ import annotations

import argparse

from . import __version__
from .server import build_server


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="html-artifact-deploy", description=__doc__)
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    parser.parse_args(argv)
    build_server().run("stdio")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
