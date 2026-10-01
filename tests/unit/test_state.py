"""html_artifact_deploy.state: schema creation, versioning, transactions."""

from __future__ import annotations

import hashlib
import sqlite3
import stat
from pathlib import Path

import pytest

from html_artifact_deploy.state import DB_FILE_NAME, StateDB, StateError, secret_hash

pytestmark = pytest.mark.unit

EXPECTED_COLUMNS = {
    "pages": ["page_id", "owner", "title", "bytes", "sha256", "created_at", "updated_at", "expires_at"],
    "oauth_clients": ["client_id", "info_json", "created_at"],
    "auth_requests": ["state_hash", "client_id", "params_json", "nonce", "binding_hash", "expires_at"],
    "auth_codes": ["code_hash", "client_id", "code_json", "hosted_domain", "family", "used_at", "expires_at"],
    "tokens": [
        "token_hash", "kind", "client_id", "subject", "hosted_domain", "scopes", "resource",
        "family", "name", "used_at", "expires_at", "created_at",
    ],
    "uploads": ["upload_hash", "owner", "state", "bytes", "expires_at"],
}  # fmt: skip
EXPECTED_INDEXES = {"pages_by_owner", "pages_by_expiry", "api_token_names", "uploads_by_owner"}


def _insert_page(connection: sqlite3.Connection, page_id: str) -> None:
    connection.execute("INSERT INTO pages VALUES (?, 'o', 't', 1, 'h', 1, 1, 1)", (page_id,))


class TestStateDB:
    def test_new_file_gets_schema_version_one_and_every_table(self, tmp_path: Path) -> None:
        db = StateDB(tmp_path / "sub" / DB_FILE_NAME)
        with db.transaction() as connection:
            assert connection.execute("PRAGMA user_version").fetchone()[0] == 1
            for table, columns in EXPECTED_COLUMNS.items():
                found = [row["name"] for row in connection.execute(f"PRAGMA table_info({table})")]
                assert found == columns
            indexes = {row["name"] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='index'")}
        assert EXPECTED_INDEXES <= indexes
        db.close()

    def test_reopening_does_not_rerun_the_schema(self, tmp_path: Path) -> None:
        path = tmp_path / DB_FILE_NAME
        first = StateDB(path)
        with first.transaction() as connection:
            _insert_page(connection, "a")
        first.close()
        second = StateDB(path)
        with second.transaction() as connection:
            assert connection.execute("SELECT count(*) FROM pages").fetchone()[0] == 1
        second.close()

    def test_unknown_schema_version_is_refused(self, tmp_path: Path) -> None:
        path = tmp_path / DB_FILE_NAME
        raw = sqlite3.connect(path)
        raw.execute("PRAGMA user_version = 7")
        raw.close()
        with pytest.raises(StateError) as exc:
            StateDB(path)
        assert str(exc.value) == f"{path} has schema version 7; this version of html-artifact-deploy understands 1."

    def test_file_mode_is_private(self, tmp_path: Path) -> None:
        path = tmp_path / DB_FILE_NAME
        StateDB(path).close()
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def test_transaction_rolls_back_on_an_exception(self, tmp_path: Path) -> None:
        db = StateDB(tmp_path / DB_FILE_NAME)
        with pytest.raises(RuntimeError), db.transaction() as connection:
            _insert_page(connection, "a")
            raise RuntimeError("boom")
        with db.transaction() as connection:
            assert connection.execute("SELECT count(*) FROM pages").fetchone()[0] == 0
        db.close()

    def test_transaction_after_close_raises(self, tmp_path: Path) -> None:
        db = StateDB(tmp_path / DB_FILE_NAME)
        db.close()
        with pytest.raises(sqlite3.ProgrammingError), db.transaction():
            pass  # pragma: no cover


class TestSecretHash:
    def test_is_sha256_hex(self) -> None:
        assert secret_hash("abc") == hashlib.sha256(b"abc").hexdigest()
