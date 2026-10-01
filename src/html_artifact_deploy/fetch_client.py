"""Download a page from an administrator-listed https host, for `publish_page_from_url`.

A server that fetches any address a model hands it can be steered at internal services (server-side
request forgery), so only host names written out in `[fetch] allowed_hosts` are fetched, each name must
resolve to a public address, every redirect is checked the same way, and the size is capped. The URL's
path and query are never logged or put in an error message: a presigned URL's query is a credential.
"""

from __future__ import annotations

import asyncio
import http.client
import ipaddress
import logging
import socket
import urllib.error
import urllib.request
from collections.abc import Callable, Sequence
from http.client import HTTPMessage
from typing import IO, Any
from urllib.parse import urlsplit

from . import __version__
from .config import FetchConfig

USER_AGENT = f"html-artifact-deploy/{__version__}"
MAX_REDIRECTS = 3
CHUNK = 65_536
_NAT64 = (ipaddress.ip_network("64:ff9b::/96"), ipaddress.ip_network("64:ff9b:1::/48"))

logger = logging.getLogger(__name__)


class FetchClientError(Exception):
    """The page could not be fetched; the message goes to the caller verbatim."""


class _AllowlistRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, client: FetchClient) -> None:
        self._client = client

    def redirect_request(
        self,
        req: urllib.request.Request,
        fp: IO[bytes],
        code: int,
        msg: str,
        headers: HTTPMessage,
        newurl: str,
    ) -> urllib.request.Request | None:
        if len(getattr(req, "redirect_dict", {})) >= MAX_REDIRECTS:
            raise FetchClientError("The address redirected too many times.")
        try:
            self._client.check_url(newurl)
        except FetchClientError:
            target = urlsplit(newurl).hostname or "an invalid address"
            raise FetchClientError(
                f"The address redirected to {target}, which this server does not fetch from."
            ) from None
        return super().redirect_request(req, fp, code, msg, headers, newurl)


class FetchClient:
    def __init__(
        self,
        config: FetchConfig,
        max_bytes: int,
        *,
        resolve: Callable[..., list[tuple[Any, ...]]] = socket.getaddrinfo,
        handlers: Sequence[urllib.request.BaseHandler] = (),
    ) -> None:
        self._config = config
        self._max_bytes = max_bytes
        self._resolve = resolve
        # ProxyHandler({}) turns off HTTPS_PROXY and friends: a proxy would resolve the name itself, which makes
        # the address check meaningless, and could add proxy credentials.
        self._opener = urllib.request.build_opener(
            urllib.request.ProxyHandler({}), _AllowlistRedirectHandler(self), *handlers
        )

    async def fetch(self, url: str) -> bytes:
        return await asyncio.to_thread(self._fetch_blocking, url)

    def check_url(self, url: str) -> str:
        """The lowercased host of `url` if this server may fetch it; `FetchClientError` otherwise."""
        invalid = FetchClientError(
            "url must be an https:// address with no user name, password or port, "
            "for example https://raw.githubusercontent.com/org/repo/main/page.html."
        )
        if any(ord(char) <= 0x20 or ord(char) == 0x7F for char in url):
            raise invalid
        try:
            parts = urlsplit(url)
            port = parts.port
        except ValueError:
            raise invalid from None
        if (
            parts.scheme != "https"
            or not parts.hostname
            or parts.username is not None
            or parts.password is not None
            or port not in (None, 443)
        ):
            raise invalid
        host = parts.hostname.lower()
        allowed = self._config.allowed_hosts
        if host not in allowed:
            raise FetchClientError(
                f"This server only fetches pages from {', '.join(allowed)}. "
                f"Ask whoever runs it to add {host} to fetch.allowed_hosts, or pass the page as html."
            )
        try:
            infos = self._resolve(host, 443, type=socket.SOCK_STREAM)
        except OSError:
            raise FetchClientError(f"Could not look up {host}.") from None
        for info in infos:
            address = ipaddress.ip_address(str(info[4][0]).partition("%")[0])
            if not address.is_global or any(address in network for network in _NAT64 if address.version == 6):
                raise FetchClientError(
                    f"{host} resolves to a private or local address, so this server will not fetch from it."
                )
        return host

    def _fetch_blocking(self, url: str) -> bytes:
        host = self.check_url(url)
        request = urllib.request.Request(
            url, method="GET", headers={"User-Agent": USER_AGENT, "Accept": "text/html, */*;q=0.1"}
        )
        try:
            response = self._opener.open(request, timeout=self._config.timeout_seconds)
        except urllib.error.HTTPError as exc:
            exc.close()
            raise FetchClientError(f"{host} answered HTTP {exc.code}.") from None
        except (OSError, ValueError):
            raise FetchClientError(f"Could not fetch the page from {host}.") from None
        limit = self._max_bytes
        too_big = FetchClientError(f"The page at that address is over the {limit:,}-byte limit.")
        try:
            length = response.headers.get("Content-Length", "")
            if length.isdigit() and int(length) > limit:
                raise too_big
            chunks: list[bytes] = []
            total = 0
            try:
                while chunk := response.read(CHUNK):
                    total += len(chunk)
                    if total > limit:
                        raise too_big
                    chunks.append(chunk)
            except (OSError, http.client.HTTPException):
                raise FetchClientError(f"Could not fetch the page from {host}.") from None
        finally:
            response.close()
        data = b"".join(chunks)
        logger.info("fetched %d bytes from %s", len(data), host)
        return data
