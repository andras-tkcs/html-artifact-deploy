"""The Streamable HTTP application: the tools behind sign-in, host checks and a body limit.

`build_http_app` wires the OAuth authorization server (`oauth_provider.py`), the three page tools
and a health route into one ASGI app. MCP sessions are stateless and answers are plain JSON, so a
restart loses nothing and no server-to-client stream has to stay open behind the reverse proxy.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Mapping
from urllib.parse import urlsplit

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import DEFAULT_MAX_REQUEST_BODY_SIZE, TransportSecuritySettings
from starlette.applications import Starlette
from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from .config import Config, google_client_secret, require_http
from .google_oidc_client import GoogleOidcClient
from .oauth_provider import CALLBACK_PATH, START_PATH, GoogleOAuthProvider, auth_settings
from .page_index import PageIndex
from .server import AppContext, build_server
from .service import PageService
from .state import DB_FILE_NAME, StateDB
from .storage import LocalFolderStore
from .token_store import TokenStore

MCP_PATH = "/mcp"
HEALTH_PATH = "/healthz"


def max_request_body_size(max_bytes: int) -> int:
    """The largest request body: a page of `max_bytes` can be JSON-escaped to about twice its size."""
    return max(DEFAULT_MAX_REQUEST_BODY_SIZE, 2 * max_bytes + 1_048_576)


def _owner() -> str:
    token = get_access_token()
    if token is None or token.subject is None:
        raise ToolError("Not signed in.")
    return token.subject


async def _health(request: Request) -> Response:
    return PlainTextResponse("ok")


def build_http_app(
    config: Config,
    *,
    environ: Mapping[str, str] = os.environ,
    google: GoogleOidcClient | None = None,
    clock: Callable[[], float] = time.time,
) -> Starlette:
    """Build the ASGI app `serve-http` runs; raises `ConfigError`, `StorageError` or `StateError`."""
    http, auth = require_http(config)
    db = StateDB(config.state_dir / DB_FILE_NAME)
    store = LocalFolderStore(config.pages.root)
    store.check()
    service = PageService(config.pages, store, PageIndex(db, clock=clock))
    tokens = TokenStore(db, clock=clock)
    google = google or GoogleOidcClient(auth.google_client_id, google_client_secret(auth, environ))
    provider = GoogleOAuthProvider(auth, http, tokens, google, clock=clock)
    # The SDK's protocol has an optional member, exchange_identity_assertion, that the provider omits; the
    # SDK calls it only when AuthSettings.identity_assertion_enabled is set, and auth_settings leaves it off.
    server = build_server(
        AppContext(service, _owner),
        auth_server_provider=provider,  # type: ignore[arg-type]
        auth=auth_settings(http),
    )
    # Every custom_route call must come before streamable_http_app: the SDK copies the route list then.
    server.custom_route(START_PATH, ["GET"])(provider.handle_google_start)
    server.custom_route(CALLBACK_PATH, ["GET"])(provider.handle_google_callback)
    server.custom_route(HEALTH_PATH, ["GET"])(_health)
    public_host = urlsplit(http.public_url).netloc
    return server.streamable_http_app(
        streamable_http_path=MCP_PATH,
        json_response=True,
        stateless_http=True,
        max_request_body_size=max_request_body_size(config.pages.max_bytes),
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[public_host, f"{public_host}:*", "127.0.0.1:*", "localhost:*"],
            allowed_origins=[
                http.public_url,
                "https://claude.ai",
                "https://claude.com",
                "http://127.0.0.1:*",
                "http://localhost:*",
            ],
        ),
        host=http.listen_host,
    )
