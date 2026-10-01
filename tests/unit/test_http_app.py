"""html_artifact_deploy.http_app: the assembled ASGI app, driven in-process over its real routes."""

from __future__ import annotations

import sqlite3
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx2
import pytest
from mcp.server.auth.middleware.auth_context import auth_context_var
from mcp.server.auth.middleware.bearer_auth import AuthenticatedUser
from mcp.server.auth.provider import AccessToken
from mcp.server.mcpserver.exceptions import ToolError
from mcp.server.transport_security import DEFAULT_MAX_REQUEST_BODY_SIZE
from starlette.applications import Starlette

from html_artifact_deploy.config import AuthConfig, Config, ConfigError, HttpConfig, PagesConfig
from html_artifact_deploy.http_app import _owner, build_http_app, max_request_body_size
from html_artifact_deploy.oauth_provider import START_PATH
from html_artifact_deploy.state import DB_FILE_NAME, StateDB
from html_artifact_deploy.token_store import TokenStore

pytestmark = pytest.mark.unit

PUBLIC_URL = "https://publish.example.com"
SECRET_ENV = "HTML_ARTIFACT_DEPLOY_GOOGLE_CLIENT_SECRET"
ENVIRON = {SECRET_ENV: "secret"}
ACCEPT = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}


def _config(tmp_path: Path, *, http: bool = True) -> Config:
    pages = tmp_path / "pages"
    pages.mkdir(exist_ok=True)
    return Config(
        path=tmp_path / "config.toml",
        pages=PagesConfig(root=pages, public_base_url="https://pages.example.com"),
        state_dir=tmp_path / "state",
        http=HttpConfig(public_url=PUBLIC_URL) if http else None,
        auth=AuthConfig(
            google_client_id="x.apps.googleusercontent.com",
            allowed_domains=("example.com",),
            allowed_emails=(),
        ),
    )


@asynccontextmanager
async def _client(
    app: Starlette, token: str | None = None, host: str | None = None
) -> AsyncIterator[httpx2.AsyncClient]:
    headers = dict(ACCEPT)
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if host:
        headers["Host"] = host
    async with app.router.lifespan_context(app):
        transport = httpx2.ASGITransport(app=app)
        async with httpx2.AsyncClient(transport=transport, base_url=PUBLIC_URL, headers=headers) as client:
            yield client


def _rpc(method: str, params: dict[str, object] | None = None, id: int = 1) -> dict[str, object]:
    return {"jsonrpc": "2.0", "id": id, "method": method, "params": params or {}}


_INIT = {
    "protocolVersion": "2025-06-18",
    "capabilities": {},
    "clientInfo": {"name": "t", "version": "1"},
}


def _token(tmp_path: Path, subject: str = "Person@example.com") -> str:
    return TokenStore(StateDB(tmp_path / "state" / DB_FILE_NAME)).create_api_token(subject, "t", 1)


class TestRoutes:
    async def test_healthz(self, tmp_path: Path) -> None:
        async with _client(build_http_app(_config(tmp_path), environ=ENVIRON)) as client:
            response = await client.get("/healthz")
        assert (response.status_code, response.text) == (200, "ok")

    async def test_authorization_server_metadata(self, tmp_path: Path) -> None:
        async with _client(build_http_app(_config(tmp_path), environ=ENVIRON)) as client:
            response = await client.get("/.well-known/oauth-authorization-server")
        body = response.json()
        assert body["issuer"] == PUBLIC_URL
        assert body["registration_endpoint"]

    async def test_protected_resource_metadata(self, tmp_path: Path) -> None:
        async with _client(build_http_app(_config(tmp_path), environ=ENVIRON)) as client:
            response = await client.get("/.well-known/oauth-protected-resource/mcp")
        assert response.json()["resource"] == PUBLIC_URL + "/mcp"

    async def test_sign_in_start_route_is_mounted(self, tmp_path: Path) -> None:
        async with _client(build_http_app(_config(tmp_path), environ=ENVIRON)) as client:
            response = await client.get(START_PATH, params={"state": "nope"})
        assert response.status_code == 400


class TestMcp:
    async def test_no_token_is_401(self, tmp_path: Path) -> None:
        async with _client(build_http_app(_config(tmp_path), environ=ENVIRON)) as client:
            response = await client.post("/mcp", json=_rpc("initialize", _INIT))
        assert response.status_code == 401

    async def test_api_token_lists_tools_and_publishes_for_its_subject(self, tmp_path: Path) -> None:
        app = build_http_app(_config(tmp_path), environ=ENVIRON)
        token = _token(tmp_path)
        async with _client(app, token) as client:
            tools = await client.post("/mcp", json=_rpc("tools/list"))
            names = {tool["name"] for tool in tools.json()["result"]["tools"]}
            info = await client.post("/mcp", json=_rpc("tools/call", {"name": "server_info", "arguments": {}}, 2))
            published = await client.post(
                "/mcp",
                json=_rpc(
                    "tools/call",
                    {"name": "publish_page", "arguments": {"title": "Hi", "html": "<p>hi</p>"}},
                    3,
                ),
            )
        assert {"publish_page", "list_pages", "unpublish_page", "server_info"} <= names
        assert info.json()["result"]["structuredContent"]["name"] == "html-artifact-deploy"
        page_id = published.json()["result"]["structuredContent"]["page_id"]
        assert (tmp_path / "pages" / page_id / "index.html").read_text() == "<p>hi</p>"
        connection = sqlite3.connect(tmp_path / "state" / DB_FILE_NAME)
        owners = [row[0] for row in connection.execute("SELECT owner FROM pages WHERE page_id = ?", (page_id,))]
        connection.close()
        assert owners == ["person@example.com"]

    async def test_foreign_host_is_421(self, tmp_path: Path) -> None:
        app = build_http_app(_config(tmp_path), environ=ENVIRON)
        async with _client(app, _token(tmp_path), host="evil.example") as client:
            response = await client.post("/mcp", json=_rpc("tools/list"))
        assert response.status_code == 421


class TestBodySize:
    def test_small_max_bytes_keeps_the_sdk_default(self) -> None:
        assert max_request_body_size(1000) == DEFAULT_MAX_REQUEST_BODY_SIZE

    def test_large_max_bytes_doubles_plus_a_megabyte(self) -> None:
        assert max_request_body_size(16_000_000) == 32_000_000 + 1_048_576


class TestBuild:
    def test_requires_the_http_section(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match="needs the \\[http\\] and \\[auth\\] sections"):
            build_http_app(_config(tmp_path, http=False), environ=ENVIRON)

    def test_requires_the_secret(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match=SECRET_ENV):
            build_http_app(_config(tmp_path), environ={SECRET_ENV: ""})


class TestOwner:
    def test_no_token_is_not_signed_in(self) -> None:
        with pytest.raises(ToolError, match="Not signed in."):
            _owner()

    def test_token_without_a_subject_is_not_signed_in(self) -> None:
        token = AccessToken(token="t", client_id="c", scopes=["pages"], expires_at=None)  # nosec B106
        reset = auth_context_var.set(AuthenticatedUser(token))
        try:
            with pytest.raises(ToolError, match="Not signed in."):
                _owner()
        finally:
            auth_context_var.reset(reset)
