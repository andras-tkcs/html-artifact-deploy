"""Command-line entry point: `python -m html_artifact_deploy` or the `html-artifact-deploy` script."""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from collections.abc import Mapping
from datetime import UTC, datetime

from starlette.applications import Starlette

from . import __version__
from .config import (
    Config,
    ConfigError,
    HttpConfig,
    google_client_secret,
    is_allowed_email,
    load_config,
    require_http,
    resolve_config_path,
)
from .fetch_client import FetchClient
from .http_app import build_http_app
from .page_index import PageIndex
from .server import AppContext, build_server
from .service import PageService
from .state import DB_FILE_NAME, StateDB, StateError
from .storage import LocalFolderStore, StorageError
from .token_store import TokenStore

DEFAULT_TOKEN_DAYS = 365
MAX_TOKEN_DAYS = 3650
PURGE_CLI_BATCH = 10_000


def _run_uvicorn(app: Starlette, http: HttpConfig) -> None:  # pragma: no cover
    import uvicorn

    # No access log: request lines would carry upload URLs and Google's `code`, which are secrets.
    uvicorn.run(
        app,
        host=http.listen_host,
        port=http.listen_port,
        proxy_headers=True,
        forwarded_allow_ips="127.0.0.1",
        log_level="info",
        access_log=False,
    )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="html-artifact-deploy", description=__doc__)
    parser.add_argument("--config", metavar="PATH", help="the configuration file (TOML)")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", metavar="COMMAND")
    commands.add_parser("serve-http", help="serve the tools over Streamable HTTP with Google sign-in")
    commands.add_parser("check-config", help="validate the configuration file and the folders it names")
    commands.add_parser("purge-expired", help="delete expired pages now")
    token = commands.add_parser("token", help="manage API tokens")
    actions = token.add_subparsers(dest="action", metavar="ACTION", required=True)
    create = actions.add_parser("create", help="create an API token")
    create.add_argument("--user", required=True, metavar="EMAIL")
    create.add_argument("--name", required=True)
    create.add_argument(
        "--days",
        type=int,
        default=DEFAULT_TOKEN_DAYS,
        choices=range(1, MAX_TOKEN_DAYS + 1),
        metavar=f"1..{MAX_TOKEN_DAYS}",
        help=f"days until it expires (default {DEFAULT_TOKEN_DAYS})",
    )
    actions.add_parser("list", help="list API tokens")
    revoke = actions.add_parser("revoke", help="revoke an API token")
    revoke.add_argument("--user", required=True, metavar="EMAIL")
    revoke.add_argument("--name", required=True)
    return parser


def _iso(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, UTC).isoformat(timespec="seconds")


def _error(message: object) -> int:
    print(f"html-artifact-deploy: {message}", file=sys.stderr)
    return 2


def _stdio(config: Config) -> int:
    store = LocalFolderStore(config.pages.root)
    store.check()
    index = PageIndex(StateDB(config.state_dir / DB_FILE_NAME))
    service = PageService(config.pages, store, index)
    fetcher = FetchClient(config.fetch, config.pages.max_bytes) if config.fetch is not None else None
    build_server(AppContext(service, owner=lambda: config.stdio_user, fetcher=fetcher)).run("stdio")
    return 0


def _serve_http(config: Config, env: Mapping[str, str]) -> int:
    http, _ = require_http(config)
    app = build_http_app(config, environ=env)
    _run_uvicorn(app, http)
    return 0


def _check_config(config: Config, env: Mapping[str, str]) -> int:
    LocalFolderStore(config.pages.root).check()
    StateDB(config.state_dir / DB_FILE_NAME)
    if config.auth is not None:
        google_client_secret(config.auth, env)
    print(f"Configuration OK: {config.path}")
    return 0


def _purge_expired(config: Config) -> int:
    store = LocalFolderStore(config.pages.root)
    store.check()
    service = PageService(config.pages, store, PageIndex(StateDB(config.state_dir / DB_FILE_NAME)))
    deleted = 0
    while True:
        count = asyncio.run(service.purge_expired(limit=PURGE_CLI_BATCH))
        deleted += count
        if count < PURGE_CLI_BATCH:
            break
    print(f"Deleted {deleted} expired pages.")
    return 0


def _token(config: Config, args: argparse.Namespace) -> int:
    _, auth = require_http(config)
    tokens = TokenStore(StateDB(config.state_dir / DB_FILE_NAME))
    if args.action == "list":
        for subject, name, expires_at, created_at in tokens.list_api_tokens():
            print(f"{subject}\t{name}\t{_iso(expires_at)}\t{_iso(created_at)}")
        return 0
    user = args.user.lower()
    if args.action == "revoke":
        if tokens.revoke_api_token(user, args.name):
            print("Revoked.")
            return 0
        print("html-artifact-deploy: no such token.", file=sys.stderr)
        return 1
    if not is_allowed_email(auth, user, user.rpartition("@")[2]):
        return _error(f"{user} is not in auth.allowed_emails or auth.allowed_domains.")
    try:
        token = tokens.create_api_token(user, args.name, args.days)
    except ValueError as exc:
        return _error(exc)
    print(token)
    print(f"Store this token now; it is not shown again. Use it as: Authorization: Bearer {token}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    args = _parser().parse_args(argv)
    env = os.environ if environ is None else environ
    try:
        config = load_config(resolve_config_path(args.config, env), env)
        if args.command == "serve-http":
            return _serve_http(config, env)
        if args.command == "check-config":
            return _check_config(config, env)
        if args.command == "purge-expired":
            return _purge_expired(config)
        if args.command == "token":
            return _token(config, args)
        return _stdio(config)
    except (ConfigError, StorageError, StateError) as exc:
        return _error(exc)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
