"""One-time upload URLs, so a large page travels as an HTTP body instead of as a tool argument.

`create_page_upload` hands out a URL whose random id is the capability; the file is PUT to it
without a bearer token, and `publish_page` then reads it by `upload_id` for the signed-in caller who
created the slot. A slot lives `UPLOAD_TTL` seconds and takes one file. The id is stored only as its
`secret_hash`, and the file is named after that hash, so neither the database nor the folder holds
the URL. All database and file work runs in worker threads, off the event loop.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import secrets
import tempfile
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import BinaryIO

from starlette.requests import Request
from starlette.responses import PlainTextResponse, Response

from .state import StateDB, secret_hash

UPLOAD_TTL = 900
UPLOAD_PATH = "/uploads/{upload_id}"
MAX_OPEN_UPLOADS = 10
_PART_SUFFIX = ".part"


class UploadError(Exception):
    """An upload problem whose message goes to the caller verbatim."""


@dataclass(frozen=True)
class UploadSlot:
    upload_id: str
    upload_url: str
    max_bytes: int
    expires_at: int


class UploadStore:
    def __init__(
        self,
        db: StateDB,
        folder: Path,
        public_url: str,
        max_bytes: int,
        *,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._db = db
        self._folder = folder
        self._public_url = public_url
        self._max_bytes = max_bytes
        self._clock = clock
        folder.mkdir(parents=True, exist_ok=True, mode=0o700)
        os.chmod(folder, 0o700)

    def _now(self) -> int:
        return int(self._clock())

    def _path(self, upload_id: str) -> Path:
        return self._folder / secret_hash(upload_id)

    def _too_big(self) -> Response:
        return PlainTextResponse(f"The file is over the {self._max_bytes:,}-byte limit.", 413)

    @staticmethod
    def _unknown() -> Response:
        return PlainTextResponse("Unknown or expired upload.", 404)

    def _create(self, owner: str) -> UploadSlot:
        now = self._now()
        with self._db.transaction() as connection:
            expired = [
                row[0] for row in connection.execute("SELECT upload_hash FROM uploads WHERE expires_at <= ?", (now,))
            ]
            connection.execute("DELETE FROM uploads WHERE expires_at <= ?", (now,))
            open_count = connection.execute("SELECT COUNT(*) FROM uploads WHERE owner = ?", (owner,)).fetchone()[0]
            if open_count >= MAX_OPEN_UPLOADS:
                limit_reached = True
            else:
                limit_reached = False
                upload_id = secrets.token_urlsafe(32)
                expires_at = now + UPLOAD_TTL
                connection.execute(
                    "INSERT INTO uploads (upload_hash, owner, state, bytes, expires_at) VALUES (?, ?, 'open', NULL, ?)",
                    (secret_hash(upload_id), owner, expires_at),
                )
        for name in expired:
            (self._folder / name).unlink(missing_ok=True)
        for part in self._folder.glob(f"*{_PART_SUFFIX}"):
            if part.stat().st_mtime < now - UPLOAD_TTL:
                part.unlink(missing_ok=True)
        if limit_reached:
            raise UploadError(
                f"You have {MAX_OPEN_UPLOADS} uploads waiting. Publish them, or wait 15 minutes for them to expire."
            )
        return UploadSlot(upload_id, f"{self._public_url}/uploads/{upload_id}", self._max_bytes, expires_at)

    async def create(self, owner: str) -> UploadSlot:
        return await asyncio.to_thread(self._create, owner)

    def _is_open(self, upload_id: str) -> bool:
        with self._db.transaction() as connection:
            row = connection.execute(
                "SELECT 1 FROM uploads WHERE upload_hash = ? AND state = 'open' AND expires_at > ?",
                (secret_hash(upload_id), self._now()),
            ).fetchone()
        return row is not None

    def _mark_filled(self, upload_id: str, size: int) -> bool:
        with self._db.transaction() as connection:
            cursor = connection.execute(
                "UPDATE uploads SET state = 'filled', bytes = ? WHERE upload_hash = ? AND state = 'open'",
                (size, secret_hash(upload_id)),
            )
        return cursor.rowcount == 1

    async def _receive(self, request: Request, handle: BinaryIO) -> int | None:
        """Write the body to `handle`; the byte count, or None when it went over the limit."""
        size = 0
        async for chunk in request.stream():
            size += len(chunk)
            if size > self._max_bytes:
                return None
            await asyncio.to_thread(handle.write, chunk)
        return size

    async def handle_put(self, request: Request) -> Response:
        upload_id = request.path_params["upload_id"]
        if not await asyncio.to_thread(self._is_open, upload_id):
            return self._unknown()
        declared = request.headers.get("content-length", "")
        if (int(declared) if declared.isdigit() else 0) > self._max_bytes:
            return self._too_big()
        descriptor, name = await asyncio.to_thread(tempfile.mkstemp, suffix=_PART_SUFFIX, dir=self._folder)
        part = Path(name)
        handle = os.fdopen(descriptor, "wb")
        keep = False
        try:
            try:
                size = await self._receive(request, handle)
            finally:
                await asyncio.to_thread(handle.close)
            if size is None:
                return self._too_big()
            if size == 0:
                return PlainTextResponse("The upload is empty.", 400)
            if not await asyncio.to_thread(self._mark_filled, upload_id, size):
                return self._unknown()
            await asyncio.to_thread(os.replace, part, self._path(upload_id))
            keep = True
        finally:
            if not keep:
                await asyncio.to_thread(part.unlink, missing_ok=True)
        return PlainTextResponse(f"Uploaded {size:,} bytes. Now call publish_page with this upload_id.", 201)

    def _read(self, owner: str, upload_id: str) -> bytes:
        with self._db.transaction() as connection:
            row = connection.execute(
                "SELECT 1 FROM uploads WHERE upload_hash = ? AND state = 'filled' AND owner = ? AND expires_at > ?",
                (secret_hash(upload_id), owner, self._now()),
            ).fetchone()
        if row is not None:
            with contextlib.suppress(FileNotFoundError):
                return self._path(upload_id).read_bytes()
        raise UploadError(
            "No uploaded file for that upload_id. "
            "Call create_page_upload, PUT the file to its upload_url, then call publish_page again."
        )

    async def read(self, owner: str, upload_id: str) -> bytes:
        return await asyncio.to_thread(self._read, owner, upload_id)

    def _discard(self, upload_id: str) -> None:
        with self._db.transaction() as connection:
            connection.execute("DELETE FROM uploads WHERE upload_hash = ?", (secret_hash(upload_id),))
        self._path(upload_id).unlink(missing_ok=True)

    async def discard(self, upload_id: str) -> None:
        await asyncio.to_thread(self._discard, upload_id)
