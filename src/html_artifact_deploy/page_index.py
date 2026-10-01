"""Who published which page, and when each page expires.

Every per-owner method treats an expired row as gone; only ``get_any`` and the purge methods
see it. No method makes an expired row live again, so a purge can delete a page's files
without racing a replacement.
"""

from __future__ import annotations

import builtins
import sqlite3
import time
from collections.abc import Callable
from dataclasses import dataclass

from html_artifact_deploy.state import StateDB


@dataclass(frozen=True)
class PageRecord:
    page_id: str
    owner: str
    title: str
    bytes: int
    sha256: str
    created_at: int
    updated_at: int
    expires_at: int


class PageIdTakenError(Exception):
    """``insert`` was given a page id that already exists."""


def _record(row: sqlite3.Row) -> PageRecord:
    return PageRecord(
        page_id=row["page_id"],
        owner=row["owner"],
        title=row["title"],
        bytes=row["bytes"],
        sha256=row["sha256"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
        expires_at=row["expires_at"],
    )


class PageIndex:
    def __init__(self, db: StateDB, *, clock: Callable[[], float] = time.time) -> None:
        self._db = db
        self.clock = clock

    def _now(self) -> int:
        return int(self.clock())

    def get(self, owner: str, page_id: str) -> PageRecord | None:
        now = self._now()
        with self._db.transaction() as connection:
            row = connection.execute(
                "SELECT page_id, owner, title, bytes, sha256, created_at, updated_at, "
                "expires_at FROM pages WHERE owner = ? AND page_id = ? AND expires_at > ?",
                (owner, page_id, now),
            ).fetchone()
        return None if row is None else _record(row)

    def get_any(self, owner: str, page_id: str) -> PageRecord | None:
        with self._db.transaction() as connection:
            row = connection.execute(
                "SELECT page_id, owner, title, bytes, sha256, created_at, updated_at, "
                "expires_at FROM pages WHERE owner = ? AND page_id = ?",
                (owner, page_id),
            ).fetchone()
        return None if row is None else _record(row)

    def exists(self, page_id: str) -> bool:
        with self._db.transaction() as connection:
            row = connection.execute("SELECT 1 FROM pages WHERE page_id = ?", (page_id,)).fetchone()
        return row is not None

    def list(self, owner: str, limit: int) -> builtins.list[PageRecord]:
        now = self._now()
        with self._db.transaction() as connection:
            rows = connection.execute(
                "SELECT page_id, owner, title, bytes, sha256, created_at, updated_at, "
                "expires_at FROM pages WHERE owner = ? AND expires_at > ? "
                "ORDER BY updated_at DESC, page_id ASC LIMIT ?",
                (owner, now, limit),
            ).fetchall()
        return [_record(row) for row in rows]

    def insert(self, record: PageRecord) -> None:
        try:
            with self._db.transaction() as connection:
                connection.execute(
                    "INSERT INTO pages (page_id, owner, title, bytes, sha256, created_at, updated_at, expires_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        record.page_id,
                        record.owner,
                        record.title,
                        record.bytes,
                        record.sha256,
                        record.created_at,
                        record.updated_at,
                        record.expires_at,
                    ),
                )
        except sqlite3.IntegrityError as error:
            raise PageIdTakenError(record.page_id) from error

    def update_live(self, record: PageRecord) -> bool:
        now = self._now()
        with self._db.transaction() as connection:
            cursor = connection.execute(
                "UPDATE pages SET title = ?, bytes = ?, sha256 = ?, updated_at = ?, expires_at = ? "
                "WHERE page_id = ? AND owner = ? AND expires_at > ?",
                (
                    record.title,
                    record.bytes,
                    record.sha256,
                    record.updated_at,
                    record.expires_at,
                    record.page_id,
                    record.owner,
                    now,
                ),
            )
        return cursor.rowcount == 1

    def set_expiry(self, owner: str, page_id: str, expires_at: int) -> bool:
        now = self._now()
        with self._db.transaction() as connection:
            cursor = connection.execute(
                "UPDATE pages SET expires_at = ? WHERE page_id = ? AND owner = ? AND expires_at > ?",
                (expires_at, page_id, owner, now),
            )
        return cursor.rowcount == 1

    def remove(self, owner: str, page_id: str) -> bool:
        with self._db.transaction() as connection:
            cursor = connection.execute("DELETE FROM pages WHERE page_id = ? AND owner = ?", (page_id, owner))
        return cursor.rowcount == 1

    def expired(self, limit: int) -> builtins.list[PageRecord]:
        now = self._now()
        with self._db.transaction() as connection:
            rows = connection.execute(
                "SELECT page_id, owner, title, bytes, sha256, created_at, updated_at, "
                "expires_at FROM pages WHERE expires_at <= ? ORDER BY expires_at ASC LIMIT ?",
                (now, limit),
            ).fetchall()
        return [_record(row) for row in rows]

    def remove_expired(self, page_id: str) -> bool:
        now = self._now()
        with self._db.transaction() as connection:
            cursor = connection.execute("DELETE FROM pages WHERE page_id = ? AND expires_at <= ?", (page_id, now))
        return cursor.rowcount == 1

    def requeue_expired(self, page_id: str) -> None:
        now = self._now()
        with self._db.transaction() as connection:
            connection.execute(
                "UPDATE pages SET expires_at = ? WHERE page_id = ? AND expires_at <= ?",
                (now, page_id, now),
            )
