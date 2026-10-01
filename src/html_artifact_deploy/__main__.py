"""Command-line entry point: `python -m html_artifact_deploy` or the `html-artifact-deploy` script."""

from __future__ import annotations

import argparse
import os
import sys
from collections.abc import Mapping

from . import __version__
from .config import ConfigError, load_config, resolve_config_path
from .page_index import PageIndex
from .server import AppContext, build_server
from .service import PageService
from .state import DB_FILE_NAME, StateDB, StateError
from .storage import LocalFolderStore, StorageError


def main(argv: list[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="html-artifact-deploy", description=__doc__)
    parser.add_argument("--config", metavar="PATH", help="the configuration file (TOML)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)
    env = os.environ if environ is None else environ
    try:
        config = load_config(resolve_config_path(args.config, env), env)
        store = LocalFolderStore(config.pages.root)
        store.check()
        index = PageIndex(StateDB(config.state_dir / DB_FILE_NAME))
    except (ConfigError, StorageError, StateError) as exc:
        print(f"html-artifact-deploy: {exc}", file=sys.stderr)
        return 2
    service = PageService(config.pages, store, index)
    build_server(AppContext(service, owner=lambda: config.stdio_user)).run("stdio")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
