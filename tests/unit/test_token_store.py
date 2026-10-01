"""html_artifact_deploy.token_store: OAuth rows, expiry, one-time use, and that no secret reaches the file."""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from mcp.server.auth.provider import AuthorizationCode, AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull
from pydantic import AnyUrl

from html_artifact_deploy.state import DB_FILE_NAME, StateDB, secret_hash
from html_artifact_deploy.token_store import (
    ACCESS_PREFIX,
    API_PREFIX,
    API_TOKEN_CLIENT_ID,
    AUTH_REQUEST_TTL,
    REFRESH_PREFIX,
    TokenStore,
)

pytestmark = pytest.mark.unit

NOW = 1_000_000


class Clock:
    def __init__(self, now: float = NOW) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def db(tmp_path: Path) -> Iterator[StateDB]:
    state = StateDB(tmp_path / DB_FILE_NAME)
    yield state
    state.close()


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def store(db: StateDB, clock: Clock) -> TokenStore:
    return TokenStore(db, clock=clock)


def client_info(client_id: str = "client-1") -> OAuthClientInformationFull:
    return OAuthClientInformationFull(
        client_id=client_id,
        client_secret="client-secret",
        redirect_uris=[AnyUrl("http://localhost:9999/cb")],
    )


def params() -> AuthorizationParams:
    return AuthorizationParams(
        state="client-state",
        scopes=["pages"],
        code_challenge="challenge",
        redirect_uri=AnyUrl("http://localhost:9999/cb"),
        redirect_uri_provided_explicitly=True,
        resource="http://127.0.0.1:8765/mcp",
    )


def auth_code(
    code: str = "code-value", client_id: str = "client-1", expires_at: float = NOW + 300
) -> AuthorizationCode:
    return AuthorizationCode(
        code=code,
        scopes=["pages"],
        expires_at=expires_at,
        client_id=client_id,
        code_challenge="challenge",
        redirect_uri=AnyUrl("http://localhost:9999/cb"),
        redirect_uri_provided_explicitly=True,
        resource="http://127.0.0.1:8765/mcp",
        subject="person@example.com",
    )


def issue(store: TokenStore, kind: str = "access", family: str = "fam", expires_at: int = NOW + 3600) -> str:
    assert kind in ("access", "refresh")
    return store.issue(
        "access" if kind == "access" else "refresh",
        "client-1",
        "person@example.com",
        "example.com",
        ["pages"],
        "http://127.0.0.1:8765/mcp",
        family,
        expires_at,
    )


class TestClients:
    def test_save_and_get_round_trip(self, store: TokenStore) -> None:
        store.save_client(client_info())
        assert store.get_client("client-1") == client_info()

    def test_unknown_client_is_none(self, store: TokenStore) -> None:
        assert store.get_client("nobody") is None

    def test_saving_again_replaces(self, store: TokenStore) -> None:
        store.save_client(client_info())
        changed = client_info().model_copy(update={"client_name": "renamed"})
        store.save_client(changed)
        found = store.get_client("client-1")
        assert found is not None and found.client_name == "renamed"


class TestAuthRequests:
    def test_get_returns_the_saved_request_without_deleting(self, store: TokenStore) -> None:
        store.save_auth_request("state-1", "client-1", params(), "nonce-1")
        assert store.get_auth_request("state-1") == ("client-1", params(), "nonce-1", None)
        assert store.get_auth_request("state-1") is not None
        assert store.count_pending_sign_ins() == 1

    def test_unknown_state_is_none(self, store: TokenStore) -> None:
        assert store.get_auth_request("nope") is None
        assert store.pop_auth_request("nope") is None

    def test_bind_binds_once(self, store: TokenStore) -> None:
        store.save_auth_request("state-1", "client-1", params(), "nonce-1")
        assert store.bind_auth_request("state-1", "binding-1") is True
        assert store.bind_auth_request("state-1", "binding-2") is False
        found = store.get_auth_request("state-1")
        assert found is not None
        assert found[3] == secret_hash("binding-1")

    def test_bind_refuses_unknown_and_expired(self, store: TokenStore, clock: Clock) -> None:
        assert store.bind_auth_request("nope", "binding") is False
        store.save_auth_request("state-1", "client-1", params(), "nonce-1")
        clock.now += AUTH_REQUEST_TTL
        assert store.bind_auth_request("state-1", "binding") is False

    def test_pop_deletes(self, store: TokenStore) -> None:
        store.save_auth_request("state-1", "client-1", params(), "nonce-1")
        assert store.pop_auth_request("state-1") == ("client-1", params(), "nonce-1", None)
        assert store.pop_auth_request("state-1") is None
        assert store.count_pending_sign_ins() == 0

    def test_expired_request_is_absent(self, store: TokenStore, clock: Clock) -> None:
        store.save_auth_request("state-1", "client-1", params(), "nonce-1")
        clock.now += AUTH_REQUEST_TTL - 1
        assert store.get_auth_request("state-1") is not None
        clock.now += 1
        assert store.get_auth_request("state-1") is None
        assert store.count_pending_sign_ins() == 0
        assert store.pop_auth_request("state-1") is None


class TestCodes:
    def test_get_code_returns_code_and_hosted_domain(self, store: TokenStore) -> None:
        store.save_code(auth_code(), "example.com")
        found = store.get_code("client-1", "code-value")
        assert found is not None
        code, hosted_domain, used_family = found
        assert code.code == "code-value"
        assert code.subject == "person@example.com"
        assert code.expires_at == NOW + 300
        assert (hosted_domain, used_family) == ("example.com", None)

    def test_other_client_unknown_or_expired_is_none(self, store: TokenStore, clock: Clock) -> None:
        store.save_code(auth_code(), None)
        assert store.get_code("client-2", "code-value") is None
        assert store.get_code("client-1", "other") is None
        clock.now = NOW + 300
        assert store.get_code("client-1", "code-value") is None

    def test_mark_code_used_once_then_reports_family(self, store: TokenStore) -> None:
        store.save_code(auth_code(), None)
        assert store.mark_code_used("code-value", "fam-1") is True
        assert store.mark_code_used("code-value", "fam-2") is False
        found = store.get_code("client-1", "code-value")
        assert found is not None and found[2] == "fam-1"

    def test_mark_unknown_code(self, store: TokenStore) -> None:
        assert store.mark_code_used("nope", "fam") is False


class TestTokens:
    def test_issue_prefixes_and_get(self, store: TokenStore) -> None:
        access = issue(store, "access")
        refresh = issue(store, "refresh")
        assert access.startswith(ACCESS_PREFIX) and refresh.startswith(REFRESH_PREFIX)
        row = store.get_token(access, ("access",))
        assert row is not None
        assert (row["kind"], row["client_id"], row["subject"], row["hosted_domain"]) == (
            "access",
            "client-1",
            "person@example.com",
            "example.com",
        )
        assert (row["scopes"], row["resource"], row["family"], row["expires_at"], row["created_at"]) == (
            "pages",
            "http://127.0.0.1:8765/mcp",
            "fam",
            NOW + 3600,
            NOW,
        )

    def test_get_token_filters_kind(self, store: TokenStore) -> None:
        access = issue(store, "access")
        assert store.get_token(access, ("refresh",)) is None
        assert store.get_token("unknown", ("access",)) is None

    def test_expired_token_is_absent(self, store: TokenStore, clock: Clock) -> None:
        access = issue(store, expires_at=NOW + 10)
        clock.now = NOW + 10
        assert store.get_token(access, ("access",)) is None

    def test_mark_used_once_and_used_rows_are_returned(self, store: TokenStore) -> None:
        refresh = issue(store, "refresh")
        assert store.mark_used(refresh) is True
        assert store.mark_used(refresh) is False
        row = store.get_token(refresh, ("refresh",))
        assert row is not None and row["used_at"] == NOW

    def test_revoke_family_removes_only_that_family(self, store: TokenStore) -> None:
        a1 = issue(store, "access", family="a")
        r1 = issue(store, "refresh", family="a")
        b1 = issue(store, "access", family="b")
        store.revoke_family("a")
        assert store.get_token(a1, ("access",)) is None
        assert store.get_token(r1, ("refresh",)) is None
        assert store.get_token(b1, ("access",)) is not None


class TestApiTokens:
    def test_create_lowercases_subject_and_lists(self, store: TokenStore) -> None:
        token = store.create_api_token("Person@Example.COM", "laptop", 30)
        assert token.startswith(API_PREFIX)
        row = store.get_token(token, ("api",))
        assert row is not None
        assert (row["subject"], row["hosted_domain"], row["client_id"], row["scopes"]) == (
            "person@example.com",
            "example.com",
            API_TOKEN_CLIENT_ID,
            "pages",
        )
        assert (row["resource"], row["family"], row["name"]) == (None, None, "laptop")
        assert store.list_api_tokens() == [("person@example.com", "laptop", NOW + 30 * 86400, NOW)]

    def test_duplicate_name_is_refused(self, store: TokenStore) -> None:
        store.create_api_token("person@example.com", "laptop", 30)
        with pytest.raises(ValueError, match=r"^person@example.com already has an API token named 'laptop'\.$"):
            store.create_api_token("PERSON@example.com", "laptop", 30)
        store.create_api_token("other@example.com", "laptop", 30)

    def test_list_skips_expired_and_sorts(self, store: TokenStore, clock: Clock) -> None:
        store.create_api_token("b@example.com", "x", 1)
        store.create_api_token("a@example.com", "y", 2)
        store.create_api_token("a@example.com", "short", 0)
        assert [(s, n) for s, n, _, _ in store.list_api_tokens()] == [("a@example.com", "y"), ("b@example.com", "x")]
        clock.now += 86400
        assert [(s, n) for s, n, _, _ in store.list_api_tokens()] == [("a@example.com", "y")]

    def test_revoke_api_token(self, store: TokenStore) -> None:
        token = store.create_api_token("person@example.com", "laptop", 30)
        assert store.revoke_api_token("Person@Example.com", "laptop") is True
        assert store.revoke_api_token("person@example.com", "laptop") is False
        assert store.get_token(token, ("api",)) is None


class TestPurgeExpired:
    def test_removes_expired_rows_and_orphaned_old_clients(self, store: TokenStore, db: StateDB, clock: Clock) -> None:
        store.save_client(client_info("orphan"))
        store.save_client(client_info("client-1"))
        clock.now = NOW + 31 * 86400
        store.save_client(client_info("recent"))
        store.save_auth_request("old", "client-1", params(), "n")
        store.save_code(auth_code("old-code", expires_at=clock.now + 1), None)
        used = issue(store, "refresh", expires_at=int(clock.now) + AUTH_REQUEST_TTL)
        store.mark_used(used)
        clock.now += AUTH_REQUEST_TTL
        live = issue(store, "access", family="live", expires_at=int(clock.now) + 1000)
        store.save_auth_request("live", "client-1", params(), "n")
        store.purge_expired()
        with db.transaction() as connection:
            assert connection.execute("SELECT COUNT(*) FROM auth_requests").fetchone()[0] == 1
            assert connection.execute("SELECT COUNT(*) FROM auth_codes").fetchone()[0] == 0
            assert connection.execute("SELECT COUNT(*) FROM tokens").fetchone()[0] == 1
            clients = {row[0] for row in connection.execute("SELECT client_id FROM oauth_clients")}
        assert clients == {"client-1", "recent"}
        assert store.get_token(live, ("access",)) is not None
        assert store.get_auth_request("live") is not None

    def test_keeps_a_client_younger_than_thirty_days(self, store: TokenStore, clock: Clock) -> None:
        store.save_client(client_info("young"))
        clock.now = NOW + 30 * 86400
        store.purge_expired()
        assert store.get_client("young") is not None


def test_no_plaintext_secret_reaches_the_database_file(tmp_path: Path) -> None:
    path = tmp_path / DB_FILE_NAME
    db = StateDB(path)
    store = TokenStore(db, clock=Clock())
    store.save_auth_request("state-secret-value", "client-1", params(), "nonce")
    assert store.bind_auth_request("state-secret-value", "binding-secret-value")
    store.save_code(auth_code("code-secret-value"), None)
    plaintexts = [
        "state-secret-value",
        "binding-secret-value",
        "code-secret-value",
        issue(store, "access"),
        issue(store, "refresh"),
        store.create_api_token("person@example.com", "laptop", 30),
    ]
    db.close()
    data = b"".join(p.read_bytes() for p in tmp_path.iterdir() if p.is_file())
    for secret in plaintexts:
        assert secret.encode() not in data
