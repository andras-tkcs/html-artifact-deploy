"""The OAuth 2.1 authorization server, with Google sign-in proving who a person is.

The SDK serves the OAuth routes (metadata, /register, /authorize, /token, /revoke) and calls this
provider. /authorize sends the browser to START_PATH, which binds the pending sign-in to that
browser with a cookie before redirecting to Google; CALLBACK_PATH checks the cookie, so a Google
link copied out of someone else's sign-in cannot finish it. Google's tokens never leave this
server: the provider issues its own opaque tokens (ADR 0015).

Only the redirect URIs in `auth.redirect_uri_allowlist` and loopback addresses may register,
which stops a stranger's client from receiving a signed-in person's code at their own site. The
allowlist of people is checked again on every token use, so removing someone from the
configuration locks them out at their next request.
"""

from __future__ import annotations

import asyncio
import hmac
import html
import logging
import secrets
import time
from collections.abc import Callable, Sequence
from urllib.parse import urlencode, urlsplit

from mcp.server.auth.provider import (
    AccessToken,
    AuthorizationCode,
    AuthorizationParams,
    AuthorizeError,
    RefreshToken,
    RegistrationError,
    TokenError,
    construct_redirect_uri,
)
from mcp.server.auth.settings import AuthSettings, ClientRegistrationOptions, RevocationOptions
from mcp.shared.auth import OAuthClientInformationFull, OAuthToken
from starlette.requests import Request
from starlette.responses import HTMLResponse, RedirectResponse, Response

from html_artifact_deploy.config import AuthConfig, HttpConfig, is_allowed_email
from html_artifact_deploy.google_oidc_client import GoogleOidcClient, GoogleOidcClientError
from html_artifact_deploy.state import secret_hash
from html_artifact_deploy.token_store import API_TOKEN_CLIENT_ID as API_TOKEN_CLIENT_ID
from html_artifact_deploy.token_store import AUTH_REQUEST_TTL as AUTH_REQUEST_TTL
from html_artifact_deploy.token_store import SCOPE, TokenStore

logger = logging.getLogger(__name__)

START_PATH = "/oauth/google/start"
CALLBACK_PATH = "/oauth/google/callback"
ACCESS_TOKEN_TTL = 3600
REFRESH_TOKEN_TTL = 30 * 86400  # absolute: a rotated refresh token keeps the expiry it replaces
AUTH_CODE_TTL = 300
MAX_PENDING_SIGN_INS = 1000
BINDING_COOKIE_HTTPS = "__Host-hda_signin"
BINDING_COOKIE_HTTP = "hda_signin"
_LOOPBACK_HOSTS = frozenset({"localhost", "127.0.0.1", "::1"})
_NO_STORE = {"Cache-Control": "no-store"}

_EXPIRED = "This sign-in link has expired or was already used. Start again from your AI client."
_OTHER_BROWSER = "This sign-in was started in a different browser. Start again from your AI client."
_GOOGLE_FAILED = "Google sign-in failed. Start again from your AI client."


def page(message: str) -> str:
    """A minimal HTML page; `message` must already be HTML-safe."""
    return f'<!doctype html><meta charset="utf-8"><title>html-artifact-deploy</title><p>{message}</p>'


def is_allowed_redirect_uri(uri: str, allowlist: Sequence[str]) -> bool:
    """Exactly in the allowlist, or plain http to a loopback host (a local client's callback)."""
    if uri in allowlist:
        return True
    parts = urlsplit(uri)
    return parts.scheme == "http" and parts.hostname in _LOOPBACK_HOSTS


def auth_settings(http: HttpConfig) -> AuthSettings:
    # Validated from plain strings so the issuer keeps its exact spelling, with no trailing slash
    # added: clients compare the issuer as a string (RFC 8414).
    return AuthSettings.model_validate(
        {
            "issuer_url": http.public_url,
            "resource_server_url": http.public_url + "/mcp",
            "service_documentation_url": None,
            "client_registration_options": ClientRegistrationOptions(
                enabled=True, valid_scopes=[SCOPE], default_scopes=[SCOPE]
            ),
            "revocation_options": RevocationOptions(enabled=True),
            "required_scopes": [SCOPE],
            "validate_token_resource": True,  # nosec B105  # a settings flag, not a secret
        }
    )


def _html(message: str, status_code: int) -> HTMLResponse:
    return HTMLResponse(page(message), status_code, headers=_NO_STORE)


class GoogleOAuthProvider:
    def __init__(
        self,
        auth: AuthConfig,
        http: HttpConfig,
        store: TokenStore,
        google: GoogleOidcClient,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._auth = auth
        self._http = http
        self._store = store
        self._google = google
        self._clock = clock
        self.callback_url = http.public_url + CALLBACK_PATH
        self.resource_url = http.public_url + "/mcp"
        self._secure_cookie = urlsplit(http.public_url).scheme == "https"
        self.binding_cookie = BINDING_COOKIE_HTTPS if self._secure_cookie else BINDING_COOKIE_HTTP

    def _now(self) -> int:
        return int(self._clock())

    def _allowed(self, subject: str, hosted_domain: str | None) -> bool:
        return is_allowed_email(self._auth, subject, hosted_domain)

    async def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        return await asyncio.to_thread(self._store.get_client, client_id)

    async def register_client(self, client_info: OAuthClientInformationFull) -> None:
        for uri in client_info.redirect_uris or []:
            if not is_allowed_redirect_uri(str(uri), self._auth.redirect_uri_allowlist):
                raise RegistrationError(
                    "invalid_redirect_uri",
                    "This server accepts only the redirect URIs of claude.ai, claude.com and local (loopback)"
                    " addresses. Ask whoever runs it to add yours to auth.redirect_uri_allowlist.",
                )
        await asyncio.to_thread(self._store.save_client, client_info)

    async def authorize(self, client: OAuthClientInformationFull, params: AuthorizationParams) -> str:
        if not is_allowed_redirect_uri(str(params.redirect_uri), self._auth.redirect_uri_allowlist):
            raise AuthorizeError("invalid_request", "This redirect URI is no longer allowed.")
        if params.resource not in (None, self.resource_url):
            raise AuthorizeError("invalid_target", "This server only issues tokens for its own /mcp endpoint.")
        await asyncio.to_thread(self._store.purge_expired)
        if await asyncio.to_thread(self._store.count_pending_sign_ins) >= MAX_PENDING_SIGN_INS:
            raise AuthorizeError(
                "temporarily_unavailable", "Too many sign-ins are in progress; try again in a few minutes."
            )
        state = secrets.token_urlsafe(32)
        nonce = secrets.token_urlsafe(32)
        await asyncio.to_thread(self._store.save_auth_request, state, client.client_id, params, nonce)
        return f"{self._http.public_url}{START_PATH}?{urlencode({'state': state})}"

    async def handle_google_start(self, request: Request) -> Response:
        """Bind the pending sign-in to this browser with a cookie, then send it to Google."""
        state = request.query_params.get("state", "")
        binding = secrets.token_urlsafe(32)
        if not await asyncio.to_thread(self._store.bind_auth_request, state, binding):
            return _html(_EXPIRED, 400)
        found = await asyncio.to_thread(self._store.get_auth_request, state)
        if found is None:  # expired between the two calls
            return _html(_EXPIRED, 400)
        nonce = found[2]
        response = RedirectResponse(
            self._google.authorization_url(state, nonce, self.callback_url), 302, headers=_NO_STORE
        )
        response.set_cookie(
            self.binding_cookie,
            binding,
            max_age=AUTH_REQUEST_TTL,
            path="/",
            secure=self._secure_cookie,
            httponly=True,
            samesite="lax",
        )
        return response

    async def handle_google_callback(self, request: Request) -> Response:
        query = request.query_params
        found = await asyncio.to_thread(self._store.pop_auth_request, query.get("state", ""))
        if found is None:
            return _html(_EXPIRED, 400)
        client_id, params, nonce, binding_hash = found
        cookie = request.cookies.get(self.binding_cookie)
        if cookie is None or binding_hash is None or not hmac.compare_digest(secret_hash(cookie), binding_hash):
            return _html(_OTHER_BROWSER, 400)
        if "error" in query:
            return RedirectResponse(
                construct_redirect_uri(str(params.redirect_uri), error="access_denied", state=params.state),
                302,
                headers=_NO_STORE,
            )
        try:
            identity = await self._google.exchange_code(query.get("code", ""), self.callback_url, nonce)
        except GoogleOidcClientError:
            return _html(_GOOGLE_FAILED, 502)
        if not self._allowed(identity.email, identity.hosted_domain):
            message = (
                f"{html.escape(identity.email)} may not publish pages on this server."
                " Ask whoever runs it to add your address or domain."
            )
            return _html(message, 403)
        code = secrets.token_urlsafe(32)
        authorization_code = AuthorizationCode(
            code=code,
            scopes=params.scopes or [SCOPE],
            expires_at=self._clock() + AUTH_CODE_TTL,
            client_id=client_id,
            code_challenge=params.code_challenge,
            redirect_uri=params.redirect_uri,
            redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
            resource=params.resource,
            subject=identity.email,
        )
        await asyncio.to_thread(self._store.save_code, authorization_code, identity.hosted_domain)
        logger.info("signed in %s for client %s", identity.email, client_id)
        response = RedirectResponse(
            construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state),
            302,
            headers=_NO_STORE,
        )
        response.delete_cookie(self.binding_cookie, path="/", secure=self._secure_cookie, httponly=True, samesite="lax")
        return response

    async def load_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: str
    ) -> AuthorizationCode | None:
        found = await asyncio.to_thread(self._store.get_code, client.client_id, authorization_code)
        if found is None:
            return None
        code, _, used_family = found
        if used_family is not None:
            # A replayed code: revoke everything issued from it (RFC 6749 section 4.1.2).
            await asyncio.to_thread(self._store.revoke_family, used_family)
            return None
        return code

    async def exchange_authorization_code(
        self, client: OAuthClientInformationFull, authorization_code: AuthorizationCode
    ) -> OAuthToken:
        found = await asyncio.to_thread(self._store.get_code, client.client_id, authorization_code.code)
        if found is None:
            raise TokenError("invalid_grant", "The authorization code has expired.")
        hosted_domain = found[1]
        family = secrets.token_urlsafe(16)
        if not await asyncio.to_thread(self._store.mark_code_used, authorization_code.code, family):
            raise TokenError("invalid_grant", "The authorization code was already used.")
        subject = authorization_code.subject or ""
        now = self._now()
        access = await asyncio.to_thread(
            self._store.issue,
            "access",
            authorization_code.client_id,
            subject,
            hosted_domain,
            authorization_code.scopes,
            authorization_code.resource,
            family,
            now + ACCESS_TOKEN_TTL,
        )
        refresh = await asyncio.to_thread(
            self._store.issue,
            "refresh",
            authorization_code.client_id,
            subject,
            hosted_domain,
            authorization_code.scopes,
            authorization_code.resource,
            family,
            now + REFRESH_TOKEN_TTL,
        )
        return OAuthToken(
            access_token=access,
            token_type="Bearer",  # nosec B106  # the OAuth token type, not a password
            expires_in=ACCESS_TOKEN_TTL,
            scope=" ".join(authorization_code.scopes),
            refresh_token=refresh,
        )

    async def load_refresh_token(self, client: OAuthClientInformationFull, refresh_token: str) -> RefreshToken | None:
        row = await asyncio.to_thread(self._store.get_token, refresh_token, ("refresh",))
        if row is None or row["client_id"] != client.client_id:
            return None
        if row["used_at"] is not None or not self._allowed(row["subject"], row["hosted_domain"]):
            # A replayed, already-rotated token, or a person no longer allowed: end the whole sign-in.
            await asyncio.to_thread(self._store.revoke_family, row["family"])
            return None
        return RefreshToken(
            token=refresh_token,
            client_id=row["client_id"],
            scopes=row["scopes"].split(),
            expires_at=row["expires_at"],
            resource=row["resource"],
            subject=row["subject"],
        )

    async def exchange_refresh_token(
        self, client: OAuthClientInformationFull, refresh_token: RefreshToken, scopes: list[str]
    ) -> OAuthToken:
        marked = await asyncio.to_thread(self._store.mark_used, refresh_token.token)
        row = await asyncio.to_thread(self._store.get_token, refresh_token.token, ("refresh",))
        if not marked or row is None:
            if row is not None:
                await asyncio.to_thread(self._store.revoke_family, row["family"])
            raise TokenError("invalid_grant", "The refresh token was already used.")
        granted = scopes or refresh_token.scopes
        access = await asyncio.to_thread(
            self._store.issue,
            "access",
            row["client_id"],
            row["subject"],
            row["hosted_domain"],
            granted,
            row["resource"],
            row["family"],
            self._now() + ACCESS_TOKEN_TTL,
        )
        refresh = await asyncio.to_thread(
            self._store.issue,
            "refresh",
            row["client_id"],
            row["subject"],
            row["hosted_domain"],
            granted,
            row["resource"],
            row["family"],
            row["expires_at"],
        )
        return OAuthToken(
            access_token=access,
            token_type="Bearer",  # nosec B106  # the OAuth token type, not a password
            expires_in=ACCESS_TOKEN_TTL,
            scope=" ".join(granted),
            refresh_token=refresh,
        )

    async def load_access_token(self, token: str) -> AccessToken | None:
        row = await asyncio.to_thread(self._store.get_token, token, ("access", "api"))
        if row is None:
            return None
        if not self._allowed(row["subject"], row["hosted_domain"]):
            if row["kind"] == "access":
                await asyncio.to_thread(self._store.revoke_family, row["family"])
            return None
        return AccessToken(
            token=token,
            client_id=row["client_id"],
            scopes=row["scopes"].split(),
            expires_at=row["expires_at"],
            resource=row["resource"] or self.resource_url,
            subject=row["subject"],
        )

    async def revoke_token(self, token: AccessToken | RefreshToken) -> None:
        # API tokens are left alone: only the `token revoke` command removes them.
        row = await asyncio.to_thread(self._store.get_token, token.token, ("access", "refresh"))
        if row is not None:
            await asyncio.to_thread(self._store.revoke_family, row["family"])
