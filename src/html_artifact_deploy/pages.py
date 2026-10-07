"""Page ids, titles and links.

A page's public address is only a random id, `<public_base_url>/<page_id>/`: no file name, title or
other readable part, so two pages never collide on a name and an address cannot be guessed from
what the page is about. The title lives only in the page index, for `list_pages`.
"""

from __future__ import annotations

import re
import secrets

PAGE_ID_RE = re.compile(r"[0-9a-f]{32}")  # used only as PAGE_ID_RE.fullmatch(value)
PAGE_FILE_NAME = "index.html"
MAX_TITLE = 200


def new_page_id() -> str:
    """Return a fresh id: 32 lowercase hex characters, 128 random bits."""
    return secrets.token_hex(16)


def is_page_id(value: str) -> bool:
    """Return whether `value` is exactly a page id."""
    return PAGE_ID_RE.fullmatch(value) is not None


def page_url(public_base_url: str, page_id: str) -> str:
    """Return the public link of a page; `ValueError` if `page_id` is not a page id."""
    if not is_page_id(page_id):
        raise ValueError("page_id must be 32 lowercase hexadecimal characters.")
    return f"{public_base_url}/{page_id}/"


def clean_title(title: str) -> str:
    """Strip `title`; `ValueError` if the result is empty or longer than `MAX_TITLE`."""
    cleaned = title.strip()
    if not 1 <= len(cleaned) <= MAX_TITLE:
        raise ValueError(f"title must be 1 to {MAX_TITLE} characters.")
    return cleaned
