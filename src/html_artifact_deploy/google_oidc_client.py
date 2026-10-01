"""Google OpenID Connect client: exchange an authorization code for the signed-in person's identity.

The ID token's signature is deliberately not verified: OpenID Connect Core 1.0 section 3.1.3.7
allows TLS server validation in its place when the token comes straight from the token endpoint
over TLS, which is the only way this client receives one.
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import time
import urllib.error
import urllib.request
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlencode

logger = logging.getLogger(__name__)

GOOGLE_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"  # nosec B105  # a public URL
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
TIMEOUT_SECONDS = 10

_GENERIC_MESSAGE = "Google sign-in failed."


class GoogleOidcClientError(Exception):
    """Sign-in failed; the message is generic and safe to show a person."""


@dataclass(frozen=True)
class GoogleIdentity:
    email: str  # lowercased
    hosted_domain: str | None  # the `hd` claim
    subject: str  # the `sub` claim


def _fail(reason: str) -> GoogleOidcClientError:
    logger.warning("Google sign-in failed: %s", reason)
    return GoogleOidcClientError(_GENERIC_MESSAGE)


def _decode_id_token(id_token: str) -> dict[str, Any]:
    """The payload of an ID token, without verifying the signature (see the module docstring)."""
    parts = id_token.split(".")
    if len(parts) != 3:
        raise ValueError("id_token is not three parts")
    payload = parts[1]
    claims = json.loads(base64.urlsafe_b64decode(payload + "=" * (-len(payload) % 4)))
    if not isinstance(claims, dict):
        raise ValueError("id_token payload is not an object")
    return claims


class GoogleOidcClient:
    def __init__(self, client_id: str, client_secret: str, *, clock: Callable[[], float] = time.time) -> None:
        self._client_id = client_id
        self._client_secret = client_secret
        self._clock = clock

    def authorization_url(self, state: str, nonce: str, redirect_uri: str) -> str:
        query = urlencode(
            {
                "response_type": "code",
                "client_id": self._client_id,
                "redirect_uri": redirect_uri,
                "scope": "openid email",
                "state": state,
                "nonce": nonce,
                "prompt": "select_account",
            }
        )
        return f"{GOOGLE_AUTHORIZATION_ENDPOINT}?{query}"

    async def exchange_code(self, code: str, redirect_uri: str, nonce: str) -> GoogleIdentity:
        form = {
            "grant_type": "authorization_code",
            "code": code,
            "redirect_uri": redirect_uri,
            "client_id": self._client_id,
            "client_secret": self._client_secret,
        }
        try:
            response = await asyncio.to_thread(self._post_token_request, form)
            id_token = response.get("id_token")
            if not isinstance(id_token, str):
                raise _fail("no id_token in the token response")
            claims = _decode_id_token(id_token)
        except GoogleOidcClientError:
            raise
        except (OSError, ValueError) as exc:
            raise _fail(type(exc).__name__) from exc
        return self._identity(claims, nonce)

    def _identity(self, claims: dict[str, Any], nonce: str) -> GoogleIdentity:
        if claims.get("iss") not in GOOGLE_ISSUERS:
            raise _fail("issuer mismatch")
        if claims.get("aud") != self._client_id:
            raise _fail("audience mismatch")
        exp = claims.get("exp")
        if not isinstance(exp, (int, float)) or exp <= self._clock():
            raise _fail("id_token expired")
        if claims.get("nonce") != nonce:
            raise _fail("nonce mismatch")
        email = claims.get("email")
        if not isinstance(email, str) or not email:
            raise _fail("no email")
        if claims.get("email_verified") is not True:
            raise _fail("email not verified")
        hosted_domain = claims.get("hd")
        return GoogleIdentity(
            email=email.lower(),
            hosted_domain=hosted_domain if isinstance(hosted_domain, str) else None,
            subject=str(claims.get("sub", "")),
        )

    def _post_token_request(self, form: dict[str, str]) -> dict[str, Any]:
        request = urllib.request.Request(
            GOOGLE_TOKEN_ENDPOINT,
            data=urlencode(form).encode(),
            method="POST",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
        )
        with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:  # nosec B310  # fixed https Google endpoint
            body = json.loads(response.read())
        if not isinstance(body, dict):
            raise ValueError("token response is not an object")
        return body
