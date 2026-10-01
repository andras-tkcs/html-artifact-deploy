"""html_artifact_deploy.oauth_provider: the OAuth 2.1 authorization server, driven through the SDK's routes.

The invariant that matters most: a token is only ever issued to the browser that started the
sign-in, for a person the configuration allows, and a replayed code or refresh token ends the
whole sign-in it came from.
"""

from __future__ import annotations

import base64
import hashlib
import html
import secrets
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, cast
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx2
import pytest
from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    IdentityAssertionParams,
    RegistrationError,
    TokenError,
)
from mcp.server.mcpserver import MCPServer
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl
from starlette.applications import Starlette
from starlette.requests import Request

from html_artifact_deploy import oauth_provider as mod
from html_artifact_deploy.config import DEFAULT_REDIRECT_URI_ALLOWLIST, AuthConfig, HttpConfig
from html_artifact_deploy.google_oidc_client import GoogleIdentity, GoogleOidcClient, GoogleOidcClientError
from html_artifact_deploy.oauth_provider import (
    ACCESS_TOKEN_TTL,
    API_TOKEN_CLIENT_ID,
    AUTH_CODE_TTL,
    BINDING_COOKIE_HTTP,
    BINDING_COOKIE_HTTPS,
    CALLBACK_PATH,
    REFRESH_TOKEN_TTL,
    START_PATH,
    GoogleOAuthProvider,
    auth_settings,
    is_allowed_redirect_uri,
    page,
)
from html_artifact_deploy.state import DB_FILE_NAME, StateDB
from html_artifact_deploy.token_store import TokenStore

pytestmark = pytest.mark.unit

PUBLIC_URL = "http://127.0.0.1:8765"
RESOURCE = PUBLIC_URL + "/mcp"
HTTP = HttpConfig(public_url=PUBLIC_URL)
CLIENT_ID = "1234-abc.apps.googleusercontent.com"
AUTH = AuthConfig(google_client_id=CLIENT_ID, allowed_domains=("example.com",), allowed_emails=("guest@other.org",))
REDIRECT = "http://localhost:9999/cb"
GOOGLE_URL = "https://accounts.google.test/auth"
NOW = 1_000_000
PERSON = GoogleIdentity(email="person@example.com", hosted_domain="example.com", subject="1")


class Clock:
    def __init__(self, now: float = NOW) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


class FakeGoogle:
    def __init__(self) -> None:
        self.identity = PERSON
        self.fail = False
        self.exchanges: list[tuple[str, str, str]] = []

    def authorization_url(self, state: str, nonce: str, redirect_uri: str) -> str:
        return f"{GOOGLE_URL}?{urlencode({'state': state, 'nonce': nonce, 'redirect_uri': redirect_uri})}"

    async def exchange_code(self, code: str, redirect_uri: str, nonce: str) -> GoogleIdentity:
        self.exchanges.append((code, redirect_uri, nonce))
        if self.fail:
            raise GoogleOidcClientError("Google sign-in failed.")
        return self.identity


@pytest.fixture
def db(tmp_path: Path) -> Iterator[StateDB]:
    state = StateDB(tmp_path / DB_FILE_NAME)
    yield state
    state.close()


@pytest.fixture
def store(db: StateDB) -> TokenStore:
    return TokenStore(db)


@pytest.fixture
def google() -> FakeGoogle:
    return FakeGoogle()


def make_provider(
    store: TokenStore,
    google: FakeGoogle,
    auth: AuthConfig = AUTH,
    http: HttpConfig = HTTP,
    clock: Callable[[], float] | None = None,
) -> GoogleOAuthProvider:
    if clock is None:
        return GoogleOAuthProvider(auth, http, store, cast(GoogleOidcClient, google))
    return GoogleOAuthProvider(auth, http, store, cast(GoogleOidcClient, google), clock=clock)


@pytest.fixture
def provider(store: TokenStore, google: FakeGoogle) -> GoogleOAuthProvider:
    return make_provider(store, google)


def build_app(provider: GoogleOAuthProvider) -> Starlette:
    server = MCPServer("t", auth_server_provider=provider, auth=auth_settings(HTTP))

    @server.tool()
    def ping() -> str:
        return "pong"

    server.custom_route(START_PATH, ["GET"])(provider.handle_google_start)
    server.custom_route(CALLBACK_PATH, ["GET"])(provider.handle_google_callback)
    return server.streamable_http_app(
        streamable_http_path="/mcp",
        json_response=True,
        stateless_http=True,
        transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False),
    )


@asynccontextmanager
async def serve(provider: GoogleOAuthProvider) -> AsyncIterator[httpx2.AsyncClient]:
    app = build_app(provider)
    async with app.router.lifespan_context(app):
        async with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=PUBLIC_URL) as http_client:
            yield http_client


def query_of(url: str) -> dict[str, str]:
    return {key: values[0] for key, values in parse_qs(urlsplit(url).query).items()}


class Registered:
    def __init__(self, body: dict[str, Any]) -> None:
        self.client_id: str = body["client_id"]
        self.client_secret: str = body["client_secret"]
        self.verifier = secrets.token_urlsafe(48)
        digest = hashlib.sha256(self.verifier.encode()).digest()
        self.challenge = base64.urlsafe_b64encode(digest).decode().rstrip("=")

    def credentials(self) -> dict[str, str]:
        return {"client_id": self.client_id, "client_secret": self.client_secret}


async def register(client: httpx2.AsyncClient, redirect_uris: list[str] | None = None) -> Registered:
    response = await client.post("/register", json={"redirect_uris": redirect_uris or [REDIRECT]})
    assert response.status_code == 201, response.text
    return Registered(response.json())


async def authorize(client: httpx2.AsyncClient, reg: Registered, **extra: str) -> httpx2.Response:
    query = {
        "client_id": reg.client_id,
        "response_type": "code",
        "code_challenge": reg.challenge,
        "code_challenge_method": "S256",
        "redirect_uri": REDIRECT,
        "state": "client-state",
        "resource": RESOURCE,
        **extra,
    }
    return await client.get("/authorize", params=query)


async def start(client: httpx2.AsyncClient, reg: Registered) -> str:
    """Authorize and open the start link; returns the sign-in state Google will send back."""
    response = await authorize(client, reg)
    assert response.status_code == 302, response.text
    location = response.headers["location"]
    assert location.startswith(PUBLIC_URL + START_PATH + "?")
    response = await client.get(location)
    assert response.status_code == 302
    assert response.headers["cache-control"] == "no-store"
    return query_of(response.headers["location"])["state"]


async def callback(client: httpx2.AsyncClient, **query: str) -> httpx2.Response:
    response = await client.get(CALLBACK_PATH, params=query)
    assert response.headers["cache-control"] == "no-store"
    return response


async def sign_in(client: httpx2.AsyncClient) -> tuple[Registered, str]:
    """Register and sign in; returns the client and the authorization code."""
    reg = await register(client)
    state = await start(client, reg)
    response = await callback(client, state=state, code="google-code")
    assert response.status_code == 302, response.text
    query = query_of(response.headers["location"])
    assert query["state"] == "client-state"
    return reg, query["code"]


async def exchange(client: httpx2.AsyncClient, reg: Registered, code: str) -> httpx2.Response:
    form = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": REDIRECT,
        "code_verifier": reg.verifier,
        "resource": RESOURCE,
        **reg.credentials(),
    }
    return await client.post("/token", data=form)


async def refresh(client: httpx2.AsyncClient, reg: Registered, token: str) -> httpx2.Response:
    form = {"grant_type": "refresh_token", "refresh_token": token, **reg.credentials()}
    return await client.post("/token", data=form)


async def tools_list(client: httpx2.AsyncClient, token: str | None) -> httpx2.Response:
    headers = {"Accept": "application/json, text/event-stream"}
    if token is not None:
        headers["Authorization"] = f"Bearer {token}"
    body = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    return await client.post("/mcp", json=body, headers=headers)


async def tokens_for(client: httpx2.AsyncClient) -> tuple[Registered, str, dict[str, Any]]:
    reg, code = await sign_in(client)
    response = await exchange(client, reg, code)
    assert response.status_code == 200, response.text
    return reg, code, response.json()


def client_info(client_id: str = "client-1", redirect_uris: list[str] | None = None) -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        client_id=client_id,
        client_secret="s",
        redirect_uris=[AnyUrl(uri) for uri in (redirect_uris or [REDIRECT])],
    )


def auth_params(redirect_uri: str = REDIRECT, resource: str | None = RESOURCE) -> AuthorizationParams:
    return AuthorizationParams(
        state="client-state",
        scopes=["pages"],
        code_challenge="challenge",
        redirect_uri=AnyUrl(redirect_uri),
        redirect_uri_provided_explicitly=True,
        resource=resource,
    )


def auth_code(code: str = "the-code", expires_at: float = NOW + AUTH_CODE_TTL) -> AuthorizationCode:
    return AuthorizationCode(
        code=code,
        scopes=["pages"],
        expires_at=expires_at,
        client_id="client-1",
        code_challenge="challenge",
        redirect_uri=AnyUrl(REDIRECT),
        redirect_uri_provided_explicitly=True,
        resource=RESOURCE,
        subject="person@example.com",
    )


class TestRedirectUriAllowlist:
    @pytest.mark.parametrize(
        "uri",
        [*DEFAULT_REDIRECT_URI_ALLOWLIST, "http://localhost:33418/callback", "http://127.0.0.1/x", "http://[::1]:5/cb"],
    )
    def test_allowed(self, uri: str) -> None:
        assert is_allowed_redirect_uri(uri, DEFAULT_REDIRECT_URI_ALLOWLIST)

    @pytest.mark.parametrize(
        "uri", ["https://evil.example/cb", "http://example.com/cb", "https://claude.ai/other", "https://localhost/cb"]
    )
    def test_refused(self, uri: str) -> None:
        assert not is_allowed_redirect_uri(uri, DEFAULT_REDIRECT_URI_ALLOWLIST)


class TestRegistration:
    async def test_one_bad_redirect_uri_refuses_the_client(
        self, provider: GoogleOAuthProvider, store: TokenStore
    ) -> None:
        async with serve(provider) as client:
            response = await client.post("/register", json={"redirect_uris": [REDIRECT, "https://evil.example/cb"]})
            assert response.status_code == 400
            body = response.json()
            assert body["error"] == "invalid_redirect_uri"
            assert body["error_description"].startswith("This server accepts only the redirect URIs of claude.ai")
            assert body["error_description"].endswith("add yours to auth.redirect_uri_allowlist.")

    async def test_direct_refusal_saves_nothing(self, provider: GoogleOAuthProvider, store: TokenStore) -> None:
        with pytest.raises(RegistrationError) as caught:
            await provider.register_client(client_info(redirect_uris=[REDIRECT, "https://evil.example/cb"]))
        assert caught.value.error == "invalid_redirect_uri"
        assert store.get_client("client-1") is None

    async def test_registers_and_loads(self, provider: GoogleOAuthProvider) -> None:
        await provider.register_client(client_info())
        assert await provider.get_client("client-1") == client_info()


class TestFullFlow:
    async def test_sign_in_tokens_refresh_and_replay(self, provider: GoogleOAuthProvider, google: FakeGoogle) -> None:
        async with serve(provider) as client:
            reg = await register(client)
            response = await authorize(client, reg)
            assert response.status_code == 302
            start_url = response.headers["location"]
            sign_in_state = query_of(start_url)["state"]

            response = await client.get(start_url)
            assert response.status_code == 302
            google_url = response.headers["location"]
            assert google_url.startswith(GOOGLE_URL + "?")
            google_query = query_of(google_url)
            assert google_query["state"] == sign_in_state
            assert google_query["nonce"]
            assert google_query["redirect_uri"] == PUBLIC_URL + CALLBACK_PATH
            cookie = response.headers["set-cookie"]
            assert cookie.startswith(BINDING_COOKIE_HTTP + "=")
            assert "HttpOnly" in cookie and "Path=/" in cookie and "Max-Age=600" in cookie
            assert "samesite=lax" in cookie.lower() and "Secure" not in cookie

            response = await callback(client, state=sign_in_state, code="google-code")
            assert response.status_code == 302
            assert google.exchanges == [("google-code", PUBLIC_URL + CALLBACK_PATH, google_query["nonce"])]
            location = response.headers["location"]
            assert location.startswith(REDIRECT + "?")
            assert query_of(location)["state"] == "client-state"
            assert 'hda_signin=""' in response.headers["set-cookie"] and "Max-Age=0" in response.headers["set-cookie"]
            code = query_of(location)["code"]

            response = await exchange(client, reg, code)
            assert response.status_code == 200, response.text
            tokens = response.json()
            assert tokens["token_type"] == "Bearer"
            assert tokens["expires_in"] == ACCESS_TOKEN_TTL
            assert tokens["scope"] == "pages"
            access, refresh_token = tokens["access_token"], tokens["refresh_token"]
            assert access.startswith("hda_") and refresh_token.startswith("hdr_")

            response = await tools_list(client, access)
            assert response.status_code == 200
            assert [tool["name"] for tool in response.json()["result"]["tools"]] == ["ping"]
            assert (await tools_list(client, None)).status_code == 401

            response = await refresh(client, reg, refresh_token)
            assert response.status_code == 200, response.text
            new_access, new_refresh = response.json()["access_token"], response.json()["refresh_token"]
            assert (await tools_list(client, new_access)).status_code == 200

            response = await refresh(client, reg, refresh_token)
            assert response.status_code == 400
            assert response.json()["error"] == "invalid_grant"
            assert (await tools_list(client, new_access)).status_code == 401
            assert (await tools_list(client, access)).status_code == 401
            assert (await refresh(client, reg, new_refresh)).json()["error"] == "invalid_grant"

    async def test_replayed_code_fails_and_revokes_its_tokens(self, provider: GoogleOAuthProvider) -> None:
        async with serve(provider) as client:
            reg, code, tokens = await tokens_for(client)
            assert (await tools_list(client, tokens["access_token"])).status_code == 200
            response = await exchange(client, reg, code)
            assert response.status_code == 400
            assert response.json()["error"] == "invalid_grant"
            assert (await tools_list(client, tokens["access_token"])).status_code == 401
            assert (await refresh(client, reg, tokens["refresh_token"])).json()["error"] == "invalid_grant"

    async def test_revoking_the_access_token_revokes_its_refresh_token(self, provider: GoogleOAuthProvider) -> None:
        async with serve(provider) as client:
            reg, _, tokens = await tokens_for(client)
            response = await client.post("/revoke", data={"token": tokens["access_token"], **reg.credentials()})
            assert response.status_code == 200
            assert (await tools_list(client, tokens["access_token"])).status_code == 401
            assert (await refresh(client, reg, tokens["refresh_token"])).json()["error"] == "invalid_grant"


class TestAuthorizeRefusals:
    async def test_other_resource_is_invalid_target(self, provider: GoogleOAuthProvider) -> None:
        async with serve(provider) as client:
            reg = await register(client)
            response = await authorize(client, reg, resource="https://elsewhere.example/mcp")
            assert response.status_code == 302
            query = query_of(response.headers["location"])
            assert query["error"] == "invalid_target"
            assert query["state"] == "client-state"

    async def test_too_many_pending_sign_ins(
        self, provider: GoogleOAuthProvider, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async with serve(provider) as client:
            monkeypatch.setattr(mod, "MAX_PENDING_SIGN_INS", 2)
            reg = await register(client)
            for _ in range(2):
                response = await authorize(client, reg)
                assert urlsplit(response.headers["location"]).path == START_PATH
            response = await authorize(client, reg)
            assert query_of(response.headers["location"])["error"] == "temporarily_unavailable"

    async def test_redirect_uri_no_longer_allowed(self, store: TokenStore, google: FakeGoogle) -> None:
        provider = make_provider(
            store, google, auth=AuthConfig(CLIENT_ID, ("example.com",), (), redirect_uri_allowlist=())
        )
        with pytest.raises(AuthorizeError) as caught:
            await provider.authorize(client_info(), auth_params(redirect_uri=DEFAULT_REDIRECT_URI_ALLOWLIST[0]))
        assert caught.value.error == "invalid_request"

    async def test_no_resource_is_allowed(self, provider: GoogleOAuthProvider) -> None:
        url = await provider.authorize(client_info(), auth_params(resource=None))
        assert url.startswith(PUBLIC_URL + START_PATH + "?state=")


class TestStart:
    async def test_unknown_state(self, provider: GoogleOAuthProvider) -> None:
        async with serve(provider) as client:
            response = await client.get(START_PATH, params={"state": "unknown"})
            assert response.status_code == 400
            assert "expired or was already used" in response.text
            assert "set-cookie" not in response.headers

    async def test_second_start_for_the_same_state(self, provider: GoogleOAuthProvider) -> None:
        async with serve(provider) as client:
            reg = await register(client)
            location = (await authorize(client, reg)).headers["location"]
            assert (await client.get(location)).status_code == 302
            response = await client.get(location)
            assert response.status_code == 400
            assert response.headers["cache-control"] == "no-store"

    async def test_request_expiring_after_binding(
        self, provider: GoogleOAuthProvider, store: TokenStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        store.save_auth_request("state-1", "client-1", auth_params(), "nonce")
        monkeypatch.setattr(store, "get_auth_request", lambda state: None)
        response = await provider.handle_google_start(_request(START_PATH, "state=state-1"))
        assert response.status_code == 400

    async def test_https_uses_a_secure_host_cookie(self, store: TokenStore, google: FakeGoogle) -> None:
        provider = make_provider(store, google, http=HttpConfig(public_url="https://publish.example.com"))
        store.save_auth_request("state-1", "client-1", auth_params(), "nonce")
        response = await provider.handle_google_start(_request(START_PATH, "state=state-1"))
        assert response.status_code == 302
        cookie = response.headers["set-cookie"]
        assert cookie.startswith(BINDING_COOKIE_HTTPS + "=")
        assert "Secure" in cookie and "Path=/" in cookie


def _request(path: str, query: str) -> Request:
    return Request({"type": "http", "method": "GET", "path": path, "query_string": query.encode(), "headers": []})


class TestCallbackRefusals:
    async def test_unknown_state(self, provider: GoogleOAuthProvider) -> None:
        async with serve(provider) as client:
            response = await callback(client, state="unknown", code="c")
            assert response.status_code == 400
            assert "expired or was already used" in response.text

    async def test_reused_state(self, provider: GoogleOAuthProvider) -> None:
        async with serve(provider) as client:
            reg = await register(client)
            state = await start(client, reg)
            assert (await callback(client, state=state, code="c")).status_code == 302
            response = await callback(client, state=state, code="c")
            assert response.status_code == 400
            assert "expired or was already used" in response.text

    async def test_no_cookie(self, provider: GoogleOAuthProvider, google: FakeGoogle) -> None:
        async with serve(provider) as client:
            reg = await register(client)
            state = await start(client, reg)
            client.cookies.clear()
            response = await callback(client, state=state, code="c")
            assert response.status_code == 400
            assert "started in a different browser" in response.text
            assert google.exchanges == []

    async def test_wrong_cookie(self, provider: GoogleOAuthProvider) -> None:
        async with serve(provider) as client:
            reg = await register(client)
            state = await start(client, reg)
            client.cookies.clear()
            client.cookies.set(BINDING_COOKIE_HTTP, "someone-else")
            response = await callback(client, state=state, code="c")
            assert response.status_code == 400
            assert "started in a different browser" in response.text

    async def test_unbound_request(self, provider: GoogleOAuthProvider, store: TokenStore) -> None:
        """A state whose start link was never opened has no binding, so no cookie can match it."""
        async with serve(provider) as client:
            store.save_auth_request("state-1", "client-1", auth_params(), "nonce")
            client.cookies.set(BINDING_COOKIE_HTTP, "anything")
            response = await callback(client, state="state-1", code="c")
            assert response.status_code == 400
            assert "started in a different browser" in response.text

    async def test_cancelled_at_google(self, provider: GoogleOAuthProvider, google: FakeGoogle) -> None:
        async with serve(provider) as client:
            reg = await register(client)
            state = await start(client, reg)
            response = await callback(client, state=state, error="access_denied")
            assert response.status_code == 302
            query = query_of(response.headers["location"])
            assert query == {"error": "access_denied", "state": "client-state"}
            assert google.exchanges == []

    async def test_google_failure(self, provider: GoogleOAuthProvider, google: FakeGoogle) -> None:
        async with serve(provider) as client:
            google.fail = True
            reg = await register(client)
            state = await start(client, reg)
            response = await callback(client, state=state, code="c")
            assert response.status_code == 502
            assert response.text == page("Google sign-in failed. Start again from your AI client.")

    async def test_address_not_allowed(
        self, provider: GoogleOAuthProvider, google: FakeGoogle, store: TokenStore
    ) -> None:
        async with serve(provider) as client:
            google.identity = GoogleIdentity(email="a&b<x>@other.org", hosted_domain="other.org", subject="2")
            reg = await register(client)
            state = await start(client, reg)
            response = await callback(client, state=state, code="c")
            assert response.status_code == 403
            assert html.escape("a&b<x>@other.org") in response.text
            assert "a&b<x>" not in response.text
            assert "may not publish pages on this server" in response.text

    async def test_no_hosted_domain_with_only_domains_configured(self, store: TokenStore, google: FakeGoogle) -> None:
        provider = make_provider(store, google, auth=AuthConfig(CLIENT_ID, ("example.com",), ()))
        google.identity = GoogleIdentity(email="person@example.com", hosted_domain=None, subject="3")
        app = build_app(provider)
        async with app.router.lifespan_context(app):
            transport = httpx2.ASGITransport(app=app)
            async with httpx2.AsyncClient(transport=transport, base_url=PUBLIC_URL) as http_client:
                reg = await register(http_client)
                state = await start(http_client, reg)
                response = await callback(http_client, state=state, code="c")
        assert response.status_code == 403


class TestAllowlistRecheck:
    async def test_removed_person_loses_access_and_refresh(
        self, provider: GoogleOAuthProvider, store: TokenStore, google: FakeGoogle
    ) -> None:
        async with serve(provider) as client:
            reg, _, tokens = await tokens_for(client)
            narrowed = make_provider(store, google, auth=AuthConfig(CLIENT_ID, ("other.org",), ()))
            registered = await narrowed.get_client(reg.client_id)
            assert registered is not None
            assert await narrowed.load_access_token(tokens["access_token"]) is None
            assert await narrowed.load_refresh_token(registered, tokens["refresh_token"]) is None
            assert store.get_token(tokens["refresh_token"], ("refresh",)) is None
            assert store.get_token(tokens["access_token"], ("access",)) is None

    async def test_refresh_alone_revokes_the_family(self, store: TokenStore, google: FakeGoogle) -> None:
        access = store.issue("access", "client-1", "person@example.com", "example.com", ["pages"], None, "f", NOW * 10)
        refresh = store.issue(
            "refresh", "client-1", "person@example.com", "example.com", ["pages"], None, "f", NOW * 10
        )
        narrowed = make_provider(store, google, auth=AuthConfig(CLIENT_ID, ("other.org",), ()))
        assert await narrowed.load_refresh_token(client_info(), refresh) is None
        assert store.get_token(access, ("access",)) is None

    async def test_removed_person_api_token_is_refused_but_kept(self, store: TokenStore, google: FakeGoogle) -> None:
        token = store.create_api_token("person@example.com", "laptop", 30)
        narrowed = make_provider(store, google, auth=AuthConfig(CLIENT_ID, ("other.org",), ()))
        assert await narrowed.load_access_token(token) is None
        assert store.get_token(token, ("api",)) is not None


class TestDirectCalls:
    @pytest.fixture
    def clock(self) -> Clock:
        return Clock()

    @pytest.fixture
    def timed(self, db: StateDB, google: FakeGoogle, clock: Clock) -> tuple[GoogleOAuthProvider, TokenStore]:
        store = TokenStore(db, clock=clock)
        return make_provider(store, google, clock=clock), store

    async def test_expired_code_is_not_loadable(
        self, timed: tuple[GoogleOAuthProvider, TokenStore], clock: Clock
    ) -> None:
        provider, store = timed
        store.save_code(auth_code(), "example.com")
        assert await provider.load_authorization_code(client_info(), "the-code") == auth_code()
        assert await provider.load_authorization_code(client_info("client-2"), "the-code") is None
        clock.now += AUTH_CODE_TTL
        assert await provider.load_authorization_code(client_info(), "the-code") is None

    async def test_code_expiring_before_exchange(
        self, timed: tuple[GoogleOAuthProvider, TokenStore], clock: Clock
    ) -> None:
        provider, store = timed
        store.save_code(auth_code(), "example.com")
        clock.now += AUTH_CODE_TTL
        with pytest.raises(TokenError) as caught:
            await provider.exchange_authorization_code(client_info(), auth_code())
        assert caught.value.error == "invalid_grant"

    async def test_access_token_expires(self, timed: tuple[GoogleOAuthProvider, TokenStore], clock: Clock) -> None:
        provider, store = timed
        store.save_code(auth_code(), "example.com")
        tokens = await provider.exchange_authorization_code(client_info(), auth_code())
        loaded = await provider.load_access_token(tokens.access_token)
        assert loaded == AccessToken(
            token=tokens.access_token,
            client_id="client-1",
            scopes=["pages"],
            expires_at=NOW + ACCESS_TOKEN_TTL,
            resource=RESOURCE,
            subject="person@example.com",
        )
        clock.now += ACCESS_TOKEN_TTL
        assert await provider.load_access_token(tokens.access_token) is None

    async def test_rotated_refresh_token_keeps_the_original_expiry(
        self, timed: tuple[GoogleOAuthProvider, TokenStore], clock: Clock
    ) -> None:
        provider, store = timed
        store.save_code(auth_code(), "example.com")
        tokens = await provider.exchange_authorization_code(client_info(), auth_code())
        assert tokens.refresh_token is not None
        clock.now += 1000
        loaded = await provider.load_refresh_token(client_info(), tokens.refresh_token)
        assert loaded is not None and loaded.expires_at == NOW + REFRESH_TOKEN_TTL
        assert loaded.subject == "person@example.com" and loaded.resource == RESOURCE
        rotated = await provider.exchange_refresh_token(client_info(), loaded, [])
        assert rotated.refresh_token is not None
        again = await provider.load_refresh_token(client_info(), rotated.refresh_token)
        assert again is not None and again.expires_at == NOW + REFRESH_TOKEN_TTL
        access = await provider.load_access_token(rotated.access_token)
        assert access is not None and access.expires_at == NOW + 1000 + ACCESS_TOKEN_TTL

    async def test_refresh_token_of_another_client_or_unknown(
        self, timed: tuple[GoogleOAuthProvider, TokenStore]
    ) -> None:
        provider, store = timed
        store.save_code(auth_code(), "example.com")
        tokens = await provider.exchange_authorization_code(client_info(), auth_code())
        assert tokens.refresh_token is not None
        assert await provider.load_refresh_token(client_info("client-2"), tokens.refresh_token) is None
        assert await provider.load_refresh_token(client_info(), "hdr_unknown") is None
        assert await provider.load_refresh_token(client_info(), tokens.refresh_token) is not None

    async def test_api_token_loads_and_is_not_revocable_here(
        self, timed: tuple[GoogleOAuthProvider, TokenStore]
    ) -> None:
        provider, store = timed
        token = store.create_api_token("Person@Example.com", "laptop", 30)
        loaded = await provider.load_access_token(token)
        assert loaded is not None
        assert (loaded.client_id, loaded.scopes, loaded.subject) == (
            API_TOKEN_CLIENT_ID,
            ["pages"],
            "person@example.com",
        )
        assert loaded.resource == RESOURCE
        await provider.revoke_token(loaded)
        assert await provider.load_access_token(token) is not None

    async def test_code_exchange_race(self, timed: tuple[GoogleOAuthProvider, TokenStore]) -> None:
        provider, store = timed
        store.save_code(auth_code(), "example.com")
        assert store.mark_code_used("the-code", "other-family")
        with pytest.raises(TokenError) as caught:
            await provider.exchange_authorization_code(client_info(), auth_code())
        assert (caught.value.error, caught.value.error_description) == (
            "invalid_grant",
            "The authorization code was already used.",
        )

    async def test_refresh_exchange_race(self, timed: tuple[GoogleOAuthProvider, TokenStore]) -> None:
        provider, store = timed
        store.save_code(auth_code(), "example.com")
        tokens = await provider.exchange_authorization_code(client_info(), auth_code())
        assert tokens.refresh_token is not None
        loaded = await provider.load_refresh_token(client_info(), tokens.refresh_token)
        assert loaded is not None
        assert store.mark_used(tokens.refresh_token)
        with pytest.raises(TokenError) as caught:
            await provider.exchange_refresh_token(client_info(), loaded, ["pages"])
        assert (caught.value.error, caught.value.error_description) == (
            "invalid_grant",
            "The refresh token was already used.",
        )
        assert await provider.load_access_token(tokens.access_token) is None

    async def test_refresh_exchange_after_the_family_was_revoked(
        self, timed: tuple[GoogleOAuthProvider, TokenStore]
    ) -> None:
        provider, store = timed
        store.save_code(auth_code(), "example.com")
        tokens = await provider.exchange_authorization_code(client_info(), auth_code())
        assert tokens.refresh_token is not None
        loaded = await provider.load_refresh_token(client_info(), tokens.refresh_token)
        assert loaded is not None
        await provider.revoke_token(loaded)
        with pytest.raises(TokenError) as caught:
            await provider.exchange_refresh_token(client_info(), loaded, ["pages"])
        assert caught.value.error == "invalid_grant"

    async def test_identity_assertion_is_refused(self, timed: tuple[GoogleOAuthProvider, TokenStore]) -> None:
        provider, _ = timed
        with pytest.raises(TokenError) as caught:
            await provider.exchange_identity_assertion(client_info(), IdentityAssertionParams(assertion="a.b.c"))
        assert caught.value.error == "unsupported_grant_type"


def test_auth_settings() -> None:
    settings = auth_settings(HTTP)
    assert str(settings.issuer_url) == PUBLIC_URL
    assert str(settings.resource_server_url) == RESOURCE
    assert settings.service_documentation_url is None
    assert settings.client_registration_options is not None
    assert settings.client_registration_options.enabled
    assert settings.client_registration_options.valid_scopes == ["pages"]
    assert settings.client_registration_options.default_scopes == ["pages"]
    assert settings.revocation_options is not None and settings.revocation_options.enabled
    assert settings.required_scopes == ["pages"]
    assert settings.validate_token_resource is True


def test_page() -> None:
    assert page("hi") == '<!doctype html><meta charset="utf-8"><title>html-artifact-deploy</title><p>hi</p>'
