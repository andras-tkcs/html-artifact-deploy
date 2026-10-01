"""The OAuth state in the SQLite file: clients, pending sign-ins, authorization codes, tokens, API tokens.

Every secret (a token, an authorization code, a sign-in `state`, a browser binding value) is
stored only as its `secret_hash`, so a copy of the database file grants nothing. A row whose
`expires_at` is not in the future is treated as absent everywhere, whether or not
`purge_expired` has removed it yet.

Tokens issued from one authorization code share a `family`, so a replayed code or a replayed,
already-rotated refresh token can revoke everything that sign-in produced in one statement.
"""

from __future__ import annotations

import json
import secrets
import sqlite3
import time
from collections.abc import Callable
from typing import Literal

from mcp.server.auth.provider import AuthorizationCode, AuthorizationParams
from mcp.shared.auth import OAuthClientInformationFull

from html_artifact_deploy.state import StateDB, secret_hash

ACCESS_PREFIX = "hda_"  # nosec B105  # a token prefix, not a secret
REFRESH_PREFIX = "hdr_"  # nosec B105  # a token prefix, not a secret
API_PREFIX = "hdk_"  # nosec B105  # a token prefix, not a secret
API_TOKEN_CLIENT_ID = "api-token"  # nosec B105  # a client id label
SCOPE = "pages"
AUTH_REQUEST_TTL = 600
CLIENT_RETENTION_SECONDS = 30 * 86400

_PREFIXES = {"access": ACCESS_PREFIX, "refresh": REFRESH_PREFIX}

AuthRequest = tuple[str, AuthorizationParams, str, str | None]


class TokenStore:
    def __init__(self, db: StateDB, *, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self._clock = clock

    def _now(self) -> int:
        return int(self._clock())

    def save_client(self, info: OAuthClientInformationFull) -> None:
        with self._db.transaction() as connection:
            connection.execute(
                "INSERT OR REPLACE INTO oauth_clients (client_id, info_json, created_at) VALUES (?, ?, ?)",
                (info.client_id, info.model_dump_json(), self._now()),
            )

    def get_client(self, client_id: str) -> OAuthClientInformationFull | None:
        with self._db.transaction() as connection:
            row = connection.execute("SELECT info_json FROM oauth_clients WHERE client_id = ?", (client_id,)).fetchone()
        if row is None:
            return None
        return OAuthClientInformationFull.model_validate_json(row["info_json"])

    def count_pending_sign_ins(self) -> int:
        with self._db.transaction() as connection:
            row = connection.execute(
                "SELECT COUNT(*) FROM auth_requests WHERE expires_at > ?", (self._now(),)
            ).fetchone()
        return int(row[0])

    def save_auth_request(self, state: str, client_id: str, params: AuthorizationParams, nonce: str) -> None:
        with self._db.transaction() as connection:
            connection.execute(
                "INSERT INTO auth_requests (state_hash, client_id, params_json, nonce, binding_hash, expires_at)"
                " VALUES (?, ?, ?, ?, NULL, ?)",
                (secret_hash(state), client_id, params.model_dump_json(), nonce, self._now() + AUTH_REQUEST_TTL),
            )

    def _read_auth_request(self, connection: sqlite3.Connection, state: str) -> AuthRequest | None:
        row = connection.execute(
            "SELECT client_id, params_json, nonce, binding_hash FROM auth_requests"
            " WHERE state_hash = ? AND expires_at > ?",
            (secret_hash(state), self._now()),
        ).fetchone()
        if row is None:
            return None
        params = AuthorizationParams.model_validate_json(row["params_json"])
        return row["client_id"], params, row["nonce"], row["binding_hash"]

    def get_auth_request(self, state: str) -> AuthRequest | None:
        with self._db.transaction() as connection:
            return self._read_auth_request(connection, state)

    def bind_auth_request(self, state: str, binding: str) -> bool:
        """Set the browser binding once; False if the request is absent, expired or already bound."""
        with self._db.transaction() as connection:
            cursor = connection.execute(
                "UPDATE auth_requests SET binding_hash = ?"
                " WHERE state_hash = ? AND binding_hash IS NULL AND expires_at > ?",
                (secret_hash(binding), secret_hash(state), self._now()),
            )
        return cursor.rowcount == 1

    def pop_auth_request(self, state: str) -> AuthRequest | None:
        """(client_id, params, nonce, binding_hash), deleting the row; None if absent or expired."""
        with self._db.transaction() as connection:
            found = self._read_auth_request(connection, state)
            connection.execute("DELETE FROM auth_requests WHERE state_hash = ?", (secret_hash(state),))
        return found

    def save_code(self, code: AuthorizationCode, hosted_domain: str | None) -> None:
        # The code itself is left out of the stored JSON: only its hash may reach the file.
        with self._db.transaction() as connection:
            connection.execute(
                "INSERT INTO auth_codes (code_hash, client_id, code_json, hosted_domain, family, used_at, expires_at)"
                " VALUES (?, ?, ?, ?, NULL, NULL, ?)",
                (
                    secret_hash(code.code),
                    code.client_id,
                    code.model_dump_json(exclude={"code"}),
                    hosted_domain,
                    int(code.expires_at),
                ),
            )

    def get_code(self, client_id: str, code: str) -> tuple[AuthorizationCode, str | None, str | None] | None:
        """(code, hosted_domain, used_family); None if absent, another client's, or expired.

        `used_family` is the token family issued from the code if it was already exchanged.
        """
        with self._db.transaction() as connection:
            row = connection.execute(
                "SELECT code_json, hosted_domain, family, used_at FROM auth_codes"
                " WHERE code_hash = ? AND client_id = ? AND expires_at > ?",
                (secret_hash(code), client_id, self._now()),
            ).fetchone()
        if row is None:
            return None
        loaded = AuthorizationCode.model_validate({**json.loads(row["code_json"]), "code": code})
        used_family = row["family"] if row["used_at"] is not None else None
        return loaded, row["hosted_domain"], used_family

    def mark_code_used(self, code: str, family: str) -> bool:
        """True if this call marked the code used; False if it was already used or does not exist."""
        with self._db.transaction() as connection:
            cursor = connection.execute(
                "UPDATE auth_codes SET used_at = ?, family = ? WHERE code_hash = ? AND used_at IS NULL",
                (self._now(), family, secret_hash(code)),
            )
        return cursor.rowcount == 1

    def issue(
        self,
        kind: Literal["access", "refresh"],
        client_id: str,
        subject: str,
        hosted_domain: str | None,
        scopes: list[str],
        resource: str | None,
        family: str,
        expires_at: int,
    ) -> str:
        token = _PREFIXES[kind] + secrets.token_urlsafe(32)
        with self._db.transaction() as connection:
            connection.execute(
                "INSERT INTO tokens (token_hash, kind, client_id, subject, hosted_domain, scopes, resource,"
                " family, name, used_at, expires_at, created_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, NULL, NULL, ?, ?)",
                (
                    secret_hash(token),
                    kind,
                    client_id,
                    subject,
                    hosted_domain,
                    " ".join(scopes),
                    resource,
                    family,
                    expires_at,
                    self._now(),
                ),
            )
        return token

    def get_token(self, token: str, kinds: tuple[str, ...]) -> sqlite3.Row | None:
        """The token's row if it is one of `kinds` and not expired; used rows are returned too."""
        with self._db.transaction() as connection:
            row: sqlite3.Row | None = connection.execute(
                "SELECT * FROM tokens WHERE token_hash = ? AND expires_at > ?",
                (secret_hash(token), self._now()),
            ).fetchone()
        if row is None or row["kind"] not in kinds:
            return None
        return row

    def mark_used(self, token: str) -> bool:
        """True if this call marked the token used; False if it was already used or does not exist."""
        with self._db.transaction() as connection:
            cursor = connection.execute(
                "UPDATE tokens SET used_at = ? WHERE token_hash = ? AND used_at IS NULL",
                (self._now(), secret_hash(token)),
            )
        return cursor.rowcount == 1

    def revoke_family(self, family: str) -> None:
        with self._db.transaction() as connection:
            connection.execute("DELETE FROM tokens WHERE family = ?", (family,))

    def create_api_token(self, subject: str, name: str, days: int) -> str:
        subject = subject.lower()
        token = API_PREFIX + secrets.token_urlsafe(32)
        now = self._now()
        try:
            with self._db.transaction() as connection:
                connection.execute(
                    "INSERT INTO tokens (token_hash, kind, client_id, subject, hosted_domain, scopes, resource,"
                    " family, name, used_at, expires_at, created_at)"
                    " VALUES (?, 'api', ?, ?, ?, ?, NULL, NULL, ?, NULL, ?, ?)",
                    (
                        secret_hash(token),
                        API_TOKEN_CLIENT_ID,
                        subject,
                        subject.rpartition("@")[2],
                        SCOPE,
                        name,
                        now + days * 86400,
                        now,
                    ),
                )
        except sqlite3.IntegrityError as exc:
            raise ValueError(f"{subject} already has an API token named {name!r}.") from exc
        return token

    def list_api_tokens(self) -> list[tuple[str, str, int, int]]:
        """(subject, name, expires_at, created_at) of every API token that has not expired."""
        with self._db.transaction() as connection:
            rows = connection.execute(
                "SELECT subject, name, expires_at, created_at FROM tokens"
                " WHERE kind = 'api' AND expires_at > ? ORDER BY subject, name",
                (self._now(),),
            ).fetchall()
        return [(row["subject"], row["name"], row["expires_at"], row["created_at"]) for row in rows]

    def revoke_api_token(self, subject: str, name: str) -> bool:
        with self._db.transaction() as connection:
            cursor = connection.execute(
                "DELETE FROM tokens WHERE kind = 'api' AND subject = ? AND name = ?",
                (subject.lower(), name),
            )
        return cursor.rowcount > 0

    def purge_expired(self) -> None:
        """Delete expired sign-ins, codes and tokens, and clients older than 30 days that no token names."""
        now = self._now()
        with self._db.transaction() as connection:
            connection.execute("DELETE FROM auth_requests WHERE expires_at <= ?", (now,))
            connection.execute("DELETE FROM auth_codes WHERE expires_at <= ?", (now,))
            connection.execute("DELETE FROM tokens WHERE expires_at <= ?", (now,))
            connection.execute(
                "DELETE FROM oauth_clients WHERE created_at < ? AND client_id NOT IN (SELECT client_id FROM tokens)",
                (now - CLIENT_RETENTION_SECONDS,),
            )
