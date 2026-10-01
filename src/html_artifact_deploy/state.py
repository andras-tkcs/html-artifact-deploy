"""The one SQLite state file: connection, schema and transactions.

Every write goes through ``StateDB.transaction()``, which serialises threads with a lock and
wraps the work in ``BEGIN IMMEDIATE`` so a half-finished change is never visible. Secrets are
stored only as ``secret_hash`` digests.
"""

from __future__ import annotations

import hashlib
import os
import sqlite3
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

SCHEMA_VERSION = 1
DB_FILE_NAME = "state.sqlite3"

_SCHEMA = """
CREATE TABLE pages (
  page_id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL,
  bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL,
  expires_at INTEGER NOT NULL);
CREATE INDEX pages_by_owner ON pages(owner, updated_at);
CREATE INDEX pages_by_expiry ON pages(expires_at);
CREATE TABLE oauth_clients (client_id TEXT PRIMARY KEY, info_json TEXT NOT NULL, created_at INTEGER NOT NULL);
CREATE TABLE auth_requests (
  state_hash TEXT PRIMARY KEY, client_id TEXT NOT NULL, params_json TEXT NOT NULL,
  nonce TEXT NOT NULL, binding_hash TEXT, expires_at INTEGER NOT NULL);
CREATE TABLE auth_codes (
  code_hash TEXT PRIMARY KEY, client_id TEXT NOT NULL, code_json TEXT NOT NULL,
  hosted_domain TEXT, family TEXT, used_at INTEGER, expires_at INTEGER NOT NULL);
CREATE TABLE tokens (
  token_hash TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('access', 'refresh', 'api')),
  client_id TEXT NOT NULL, subject TEXT NOT NULL, hosted_domain TEXT, scopes TEXT NOT NULL,
  resource TEXT, family TEXT, name TEXT, used_at INTEGER, expires_at INTEGER NOT NULL,
  created_at INTEGER NOT NULL);
CREATE UNIQUE INDEX api_token_names ON tokens(subject, name) WHERE kind = 'api';
CREATE TABLE uploads (
  upload_hash TEXT PRIMARY KEY, owner TEXT NOT NULL, state TEXT NOT NULL CHECK (state IN ('open', 'filled')),
  bytes INTEGER, expires_at INTEGER NOT NULL);
CREATE INDEX uploads_by_owner ON uploads(owner, state);
"""


class StateError(Exception):
    """The state file cannot be used by this version."""


def secret_hash(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class StateDB:
    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(path.parent, 0o700)  # mkdir leaves an existing folder (systemd creates it 0755) as it is
        self._lock = threading.Lock()
        self._connection = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._connection.row_factory = sqlite3.Row
        self._connection.execute("PRAGMA journal_mode=WAL")
        self._connection.execute("PRAGMA foreign_keys=ON")
        version = int(self._connection.execute("PRAGMA user_version").fetchone()[0])
        if version == 0:
            with self.transaction() as connection:
                for statement in _SCHEMA.split(";"):
                    if statement.strip():
                        connection.execute(statement)
                connection.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
        elif version != SCHEMA_VERSION:
            self._connection.close()
            raise StateError(
                f"{path} has schema version {version}; "
                f"this version of html-artifact-deploy understands {SCHEMA_VERSION}."
            )
        os.chmod(path, 0o600)

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            self._connection.execute("BEGIN IMMEDIATE")
            try:
                yield self._connection
            except BaseException:
                self._connection.execute("ROLLBACK")
                raise
            self._connection.execute("COMMIT")

    def close(self) -> None:
        self._connection.close()
