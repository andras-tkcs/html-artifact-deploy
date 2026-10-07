"""Where published pages are kept.

`PageStore` is the seam for a later remote backend (for example SFTP). `LocalFolderStore` writes
`<root>/<page_id>/index.html` into the folder the web server publishes, and the web server serves
`/<page_id>/` from it. Nothing outside this module, `__main__.py` and `http_app.py` names
`LocalFolderStore`.
"""

from __future__ import annotations

import contextlib
import logging
import os
import shutil
import tempfile
from pathlib import Path
from typing import Protocol

from .pages import PAGE_FILE_NAME, is_page_id

logger = logging.getLogger(__name__)

_LINK_MESSAGE = "The pages folder contains a link where a page folder should be."


class StorageError(Exception):
    """The page store failed; the message is safe to show a person."""


class PageExistsError(StorageError):
    """A new page's folder already exists."""


class PageStore(Protocol):
    def check(self) -> None:
        """Raise `StorageError` if the store is unusable."""

    def write_page(self, page_id: str, data: bytes, *, new: bool) -> None:
        """Write a page's HTML; `new=True` refuses an existing page, `new=False` requires one."""

    def delete_page(self, page_id: str) -> bool:
        """Delete a page; `False` if there was nothing to delete."""


class LocalFolderStore:
    """A page store on the local disk: `<root>/<page_id>/index.html`."""

    def __init__(self, root: Path) -> None:
        self._root = root

    def check(self) -> None:
        if not (self._root.is_dir() and os.access(self._root, os.W_OK)):
            raise StorageError(f"The pages folder {self._root} does not exist or this server cannot write to it.")

    def write_page(self, page_id: str, data: bytes, *, new: bool) -> None:
        if not is_page_id(page_id):
            raise ValueError("page_id must be 32 lowercase hexadecimal characters.")
        folder = self._root / page_id
        try:
            if new:
                try:
                    folder.mkdir(mode=0o755)
                except FileExistsError:
                    raise PageExistsError("A page folder with that id already exists.") from None
            else:
                if folder.is_symlink():
                    raise StorageError(_LINK_MESSAGE)
                if not folder.is_dir():
                    raise StorageError("The page's folder is missing from the pages folder.")
            os.chmod(folder, 0o755)  # nosec B103  # the web server must be able to enter the page folder
            self._write_file(folder, data)
        except OSError as exc:
            logger.warning("Pages folder %s failed for %s: %s", "write", page_id, exc.strerror)
            raise StorageError("The page could not be saved in the pages folder.") from exc

    @staticmethod
    def _write_file(folder: Path, data: bytes) -> None:
        fd, tmp = tempfile.mkstemp(dir=folder, prefix=".index.", suffix=".tmp")
        try:
            with os.fdopen(fd, "wb") as handle:
                handle.write(data)
                handle.flush()
                os.fsync(handle.fileno())
            os.chmod(tmp, 0o644)
            os.replace(tmp, folder / PAGE_FILE_NAME)
        except OSError:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
            raise

    def delete_page(self, page_id: str) -> bool:
        if not is_page_id(page_id):
            raise ValueError("page_id must be 32 lowercase hexadecimal characters.")
        folder = self._root / page_id
        if not folder.exists():
            return False
        if folder.is_symlink():
            raise StorageError(_LINK_MESSAGE)
        try:
            shutil.rmtree(folder)
        except OSError as exc:
            logger.warning("Pages folder %s failed for %s: %s", "delete", page_id, exc.strerror)
            raise StorageError("The page could not be removed from the pages folder.") from exc
        return True
