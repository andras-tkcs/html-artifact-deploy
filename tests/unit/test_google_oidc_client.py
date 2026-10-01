"""google_oidc_client: authorization URL, ID token checks, token request, and the live fixture's shape."""

from __future__ import annotations

import base64
import json
import logging
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import parse_qsl, urlsplit

import pytest

from html_artifact_deploy import google_oidc_client as mod
from html_artifact_deploy.google_oidc_client import (
    GOOGLE_AUTHORIZATION_ENDPOINT,
    GOOGLE_ISSUERS,
    GOOGLE_TOKEN_ENDPOINT,
    GoogleIdentity,
    GoogleOidcClient,
    GoogleOidcClientError,
)

pytestmark = pytest.mark.unit

CLIENT_ID = "client-id.apps.googleusercontent.com"
SECRET = "client-secret-value"
NONCE = "nonce-1"
REDIRECT = "https://example.test/oauth/google/callback"
NOW = 1_000_000.0
FIXTURE = (
    Path(__file__).resolve().parents[1]
    / "fixtures"
    / "live"
    / "google_openid_configuration"
    / "openid-configuration.json"
)


def b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode().rstrip("=")


def make_token(claims: dict[str, Any]) -> str:
    return f"{b64(b'{}')}.{b64(json.dumps(claims).encode())}.{b64(b'sig')}"


def good_claims(**overrides: Any) -> dict[str, Any]:
    claims: dict[str, Any] = {
        "iss": "https://accounts.google.com",
        "aud": CLIENT_ID,
        "exp": NOW + 600,
        "nonce": NONCE,
        "email": "Person@Example.COM",
        "email_verified": True,
        "hd": "example.com",
        "sub": "1234567890",
    }
    claims.update(overrides)
    return claims


def make_client() -> GoogleOidcClient:
    return GoogleOidcClient(CLIENT_ID, SECRET, clock=lambda: NOW)


def stub_post(
    monkeypatch: pytest.MonkeyPatch, client: GoogleOidcClient, response: dict[str, Any]
) -> list[dict[str, str]]:
    forms: list[dict[str, str]] = []

    def fake(form: dict[str, str]) -> dict[str, Any]:
        forms.append(form)
        return response

    monkeypatch.setattr(client, "_post_token_request", fake)
    return forms


class TestAuthorizationUrl:
    def test_contains_every_parameter(self) -> None:
        url = make_client().authorization_url("state-1", NONCE, REDIRECT)
        parts = urlsplit(url)
        assert f"{parts.scheme}://{parts.netloc}{parts.path}" == GOOGLE_AUTHORIZATION_ENDPOINT
        assert dict(parse_qsl(parts.query)) == {
            "response_type": "code",
            "client_id": CLIENT_ID,
            "redirect_uri": REDIRECT,
            "scope": "openid email",
            "state": "state-1",
            "nonce": NONCE,
            "prompt": "select_account",
        }


class TestExchangeCode:
    async def test_success(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = make_client()
        forms = stub_post(monkeypatch, client, {"id_token": make_token(good_claims())})
        identity = await client.exchange_code("the-code", REDIRECT, NONCE)
        assert identity == GoogleIdentity(email="person@example.com", hosted_domain="example.com", subject="1234567890")
        assert forms == [
            {
                "grant_type": "authorization_code",
                "code": "the-code",
                "redirect_uri": REDIRECT,
                "client_id": CLIENT_ID,
                "client_secret": SECRET,
            }
        ]

    async def test_accepts_the_bare_issuer_and_no_hosted_domain(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = make_client()
        claims = good_claims(iss="accounts.google.com", hd=None)
        stub_post(monkeypatch, client, {"id_token": make_token(claims)})
        identity = await client.exchange_code("c", REDIRECT, NONCE)
        assert identity.hosted_domain is None

    @pytest.mark.parametrize(
        "response",
        [
            {"id_token": make_token(good_claims(iss="https://evil.test"))},
            {"id_token": make_token(good_claims(aud="other-client"))},
            {"id_token": make_token(good_claims(exp=NOW - 1))},
            {"id_token": make_token(good_claims(exp="soon"))},
            {"id_token": make_token(good_claims(nonce="other"))},
            {"id_token": make_token(good_claims(email=""))},
            {"id_token": make_token(good_claims(email=None))},
            {"id_token": make_token(good_claims(email_verified=False))},
            {"id_token": make_token({k: v for k, v in good_claims().items() if k != "email_verified"})},
            {"id_token": "only.two"},
            {"id_token": "a.!!!.c"},
            {"id_token": f"a.{b64(b'not json')}.c"},
            {"id_token": f"a.{b64(b'[1]')}.c"},
            {"access_token": "x"},
        ],
        ids=[
            "wrong-iss",
            "wrong-aud",
            "expired",
            "exp-not-number",
            "nonce-mismatch",
            "empty-email",
            "missing-email",
            "email-unverified",
            "email-verified-missing",
            "two-parts",
            "bad-base64",
            "non-json",
            "non-object",
            "no-id-token",
        ],
    )
    async def test_failures_are_generic_and_logged(
        self, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, response: dict[str, Any]
    ) -> None:
        client = make_client()
        stub_post(monkeypatch, client, response)
        with caplog.at_level(logging.WARNING, logger=mod.__name__):
            with pytest.raises(GoogleOidcClientError, match=r"^Google sign-in failed\.$"):
                await client.exchange_code("secret-code-value", REDIRECT, NONCE)
        assert "Google sign-in failed" in caplog.text
        assert "secret-code-value" not in caplog.text
        if "id_token" in response:
            assert response["id_token"] not in caplog.text

    async def test_a_transport_error_is_generic(self, monkeypatch: pytest.MonkeyPatch) -> None:
        client = make_client()

        def boom(form: dict[str, str]) -> dict[str, Any]:
            raise urllib.error.URLError("down")

        monkeypatch.setattr(client, "_post_token_request", boom)
        with pytest.raises(GoogleOidcClientError, match=r"^Google sign-in failed\.$"):
            await client.exchange_code("c", REDIRECT, NONCE)


class FakeResponse:
    def __init__(self, body: bytes) -> None:
        self._body = body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *exc: object) -> None:
        return None

    def read(self) -> bytes:
        return self._body


class TestPostTokenRequest:
    def test_posts_the_form_to_the_token_endpoint(self, monkeypatch: pytest.MonkeyPatch) -> None:
        seen: list[tuple[urllib.request.Request, float]] = []

        def fake_urlopen(request: urllib.request.Request, timeout: float) -> FakeResponse:
            seen.append((request, timeout))
            return FakeResponse(b'{"id_token": "x"}')

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        assert make_client()._post_token_request({"a": "b c"}) == {"id_token": "x"}
        ((request, timeout),) = seen
        assert request.full_url == GOOGLE_TOKEN_ENDPOINT
        assert request.get_method() == "POST"
        assert request.data == b"a=b+c"
        assert timeout == 10

    def test_network_error_propagates(self, monkeypatch: pytest.MonkeyPatch) -> None:
        def fake_urlopen(request: urllib.request.Request, timeout: float) -> FakeResponse:
            raise urllib.error.URLError("down")

        monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)
        with pytest.raises(urllib.error.URLError):
            make_client()._post_token_request({})

    @pytest.mark.parametrize("body", [b"<html>", b"[1]"])
    def test_non_object_json_is_a_value_error(self, monkeypatch: pytest.MonkeyPatch, body: bytes) -> None:
        monkeypatch.setattr(urllib.request, "urlopen", lambda request, timeout: FakeResponse(body))
        with pytest.raises(ValueError):
            make_client()._post_token_request({})


class TestLiveFixtureParsing:
    def test_fixture_matches_what_the_client_assumes(self) -> None:
        config = json.loads(FIXTURE.read_text(encoding="utf-8"))
        assert config["authorization_endpoint"] == GOOGLE_AUTHORIZATION_ENDPOINT
        assert config["token_endpoint"] == GOOGLE_TOKEN_ENDPOINT
        assert config["issuer"] in GOOGLE_ISSUERS
        assert "email" in config["claims_supported"]
