"""Publish, list and take down pages: the business rules behind the MCP tools.

Every method is a coroutine; the blocking store and index calls run through `asyncio.to_thread`.
A `PageError`'s message is written for the caller and goes to the tool result verbatim.
"""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import logging
from dataclasses import dataclass, replace

from .config import PagesConfig
from .page_index import PageIdTakenError, PageIndex, PageRecord
from .pages import clean_title, new_page_id, page_url
from .storage import PageExistsError, PageStore, StorageError

logger = logging.getLogger(__name__)

NEW_ID_ATTEMPTS = 3
SECONDS_PER_DAY = 86400
MAX_LIST_LIMIT = 200


class PageError(Exception):
    """A problem the caller can act on; its message goes to the caller verbatim."""


@dataclass(frozen=True)
class PublishResult:
    record: PageRecord
    url: str
    created: bool


class PageService:
    def __init__(self, config: PagesConfig, store: PageStore, index: PageIndex) -> None:
        self._config = config
        self._store = store
        self._index = index

    def _now(self) -> int:
        return int(self._index.clock())

    def url_for(self, record: PageRecord) -> str:
        return page_url(self._config.public_base_url, record.page_id)

    @staticmethod
    def _no_such_page(page_id: str) -> PageError:
        return PageError(
            f"No page with page_id {page_id!r} was published by you, or it has expired. "
            "Call list_pages to see your pages."
        )

    @staticmethod
    def _clean_title(title: str) -> str:
        try:
            return clean_title(title)
        except ValueError as error:
            raise PageError("title must be 1 to 200 characters.") from error

    async def check_can_publish(self, owner: str, title: str, page_id: str) -> None:
        """Run the checks that need no page content: the title, and ownership of `page_id`."""
        self._clean_title(title)
        if page_id and await asyncio.to_thread(self._index.get, owner, page_id) is None:
            raise self._no_such_page(page_id)

    async def publish(self, owner: str, title: str, data: bytes, page_id: str = "") -> PublishResult:
        title = self._clean_title(title)
        if len(data) == 0:
            raise PageError("The page is empty.")
        if len(data) > self._config.max_bytes:
            raise PageError(f"The page is {len(data):,} bytes, over the {self._config.max_bytes:,}-byte limit.")
        try:
            data.decode("utf-8")
        except UnicodeDecodeError as error:
            raise PageError("The page is not UTF-8 text, so it is not an HTML page.") from error

        sha256 = hashlib.sha256(data).hexdigest()
        now = self._now()
        try:
            if page_id:
                record = await self._replace(owner, title, data, sha256, page_id, now)
                created = False
            else:
                record = await self._create(owner, title, data, sha256, now)
                created = True
        except StorageError as error:
            raise PageError(
                "The page could not be saved on the server. Try again later, or ask whoever runs "
                "html-artifact-deploy to check its log."
            ) from error
        logger.info("published page_id=%s owner=%s bytes=%d replaced=%s", record.page_id, owner, len(data), not created)
        return PublishResult(record, self.url_for(record), created)

    async def _replace(self, owner: str, title: str, data: bytes, sha256: str, page_id: str, now: int) -> PageRecord:
        existing = await asyncio.to_thread(self._index.get, owner, page_id)
        if existing is None:
            raise self._no_such_page(page_id)
        await asyncio.to_thread(self._store.write_page, page_id, data, new=False)
        record = replace(existing, title=title, bytes=len(data), sha256=sha256, updated_at=now)
        if not await asyncio.to_thread(self._index.update_live, record):
            raise self._no_such_page(page_id)
        return record

    async def _create(self, owner: str, title: str, data: bytes, sha256: str, now: int) -> PageRecord:
        expires_at = now + self._config.default_expiry_days * SECONDS_PER_DAY
        for _ in range(NEW_ID_ATTEMPTS):
            page_id = new_page_id()
            if await asyncio.to_thread(self._index.exists, page_id):
                continue
            try:
                await asyncio.to_thread(self._store.write_page, page_id, data, new=True)
            except PageExistsError:
                continue
            record = PageRecord(page_id, owner, title, len(data), sha256, now, now, expires_at)
            try:
                await asyncio.to_thread(self._index.insert, record)
            except PageIdTakenError as error:
                with contextlib.suppress(StorageError):
                    await asyncio.to_thread(self._store.delete_page, page_id)
                raise PageError("Could not allocate a new page id. Try again.") from error
            return record
        raise PageError("Could not allocate a new page id. Try again.")

    async def list_pages(self, owner: str, limit: int) -> list[PageRecord]:
        limit = max(1, min(MAX_LIST_LIMIT, limit))
        return await asyncio.to_thread(self._index.list, owner, limit)

    async def unpublish(self, owner: str, page_id: str) -> PageRecord:
        record = await asyncio.to_thread(self._index.get_any, owner, page_id)
        if record is None:
            raise self._no_such_page(page_id)
        try:
            await asyncio.to_thread(self._store.delete_page, page_id)
        except StorageError as error:
            raise PageError(
                "The page could not be removed from the server. Try again later, or ask whoever runs "
                "html-artifact-deploy to check its log."
            ) from error
        await asyncio.to_thread(self._index.remove, owner, page_id)
        logger.info("unpublished page_id=%s owner=%s", page_id, owner)
        return record
