"""The MCP server: its tools, and the factory every transport and test builds it from.

The page tools (`publish_page`, `list_pages`, `unpublish_page`) act for the caller named by
`AppContext.owner`: stdio supplies a fixed name from the configuration, and a networked transport
supplies the signed-in person. The tools never take an owner from their arguments.

`build_server()` returns a fresh `MCPServer` on every call instead of a module-level instance, so
tests never share state through it and `tests/conftest.py` has nothing to reset for it.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from mcp.server.auth.provider import OAuthAuthorizationServerProvider
from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp.types import ToolAnnotations

from . import __version__
from .fetch_client import FetchClient, FetchClientError
from .service import PageError, PageService, PublishResult
from .uploads import UploadError, UploadStore

SERVER_NAME = "html-artifact-deploy"


@dataclass(frozen=True)
class AppContext:
    service: PageService
    owner: Callable[[], str]  # who is calling; raises ToolError("Not signed in.") if unknown
    uploads: UploadStore | None = None  # None over stdio, where there is no URL to PUT to
    fetcher: FetchClient | None = None  # None without [fetch]


def _iso(timestamp: int) -> str:
    return datetime.fromtimestamp(timestamp, UTC).isoformat(timespec="seconds")


def _publish_result(result: PublishResult) -> dict[str, object]:
    record = result.record
    return {
        "page_id": record.page_id,
        "url": result.url,
        "title": record.title,
        "bytes": record.bytes,
        "created": result.created,
        "updated_at": _iso(record.updated_at),
        "expires_at": _iso(record.expires_at),
    }


def build_server(
    app: AppContext,
    *,
    auth_server_provider: OAuthAuthorizationServerProvider[Any, Any, Any] | None = None,
    auth: AuthSettings | None = None,
) -> MCPServer:
    """Build the server with every tool registered."""
    server = MCPServer(SERVER_NAME, auth_server_provider=auth_server_provider, auth=auth)

    @server.tool(
        annotations=ToolAnnotations(
            title="Publish HTML page",
            read_only_hint=False,
            destructive_hint=False,
            idempotent_hint=False,
            open_world_hint=True,
        )
    )
    async def publish_page(title: str, html: str = "", upload_id: str = "", page_id: str = "") -> dict[str, object]:
        """Publish a single-file HTML page on your organization's page server and return its link.

        Pass the whole HTML document as html, with CSS and scripts inline or loaded from public CDNs.
        For a page over about 100 KB, when create_page_upload is available, upload the file first and
        pass upload_id instead. If the page is already online, publish_page_from_url (when available)
        fetches it from its address instead. Returns page_id, url, title, bytes, created (false when an existing page
        was replaced), updated_at and expires_at. Give url to the user: anyone with that link can open
        the page until it expires. To change a page you published, call this again with its page_id;
        the page is replaced and keeps its url. Use list_pages to find a page_id, and unpublish_page to
        take a page down.

        Args:
            title: What the page is, in words, for example "Q3 report". 1 to 200 characters. It is
                shown in list_pages only; the link is a random id and never contains it.
            html: The complete HTML document. Give exactly one of html and upload_id.
            upload_id: The upload_id from create_page_upload, after the file was PUT to its upload_url.
                Empty when html is given.
            page_id: The page_id of a page you published, to replace it. Empty to publish a new page.
        """
        if bool(html) == bool(upload_id):
            raise ToolError("Give exactly one of html and upload_id.")
        uploads = app.uploads
        if upload_id and uploads is None:
            raise ToolError("Uploads are not available on this connection; pass the page as html.")
        owner = app.owner()
        try:
            if uploads is not None and upload_id:
                await app.service.check_can_publish(owner, title, page_id)
                data = await uploads.read(owner, upload_id)
                result = await app.service.publish(owner, title, data, page_id)
                await uploads.discard(upload_id)
            else:
                result = await app.service.publish(owner, title, html.encode("utf-8"), page_id)
        except (PageError, UploadError) as error:
            raise ToolError(str(error)) from error
        return _publish_result(result)

    @server.tool(
        annotations=ToolAnnotations(
            title="List my HTML pages",
            read_only_hint=True,
            destructive_hint=False,
            idempotent_hint=True,
            open_world_hint=False,
        )
    )
    async def list_pages(limit: int = 50) -> dict[str, object]:
        """List the HTML pages you published, newest first.

        Returns pages (each with page_id, title, url, bytes, created_at, updated_at and expires_at) and
        count. Only your own pages that have not expired are listed. Use a page_id with publish_page to
        replace that page, or with unpublish_page to take it down.

        Args:
            limit: The most pages to return, 1 to 200. Defaults to 50.
        """
        records = await app.service.list_pages(app.owner(), limit)
        pages = [
            {
                "page_id": record.page_id,
                "title": record.title,
                "url": app.service.url_for(record),
                "bytes": record.bytes,
                "created_at": _iso(record.created_at),
                "updated_at": _iso(record.updated_at),
                "expires_at": _iso(record.expires_at),
            }
            for record in records
        ]
        return {"pages": pages, "count": len(pages)}

    @server.tool(
        annotations=ToolAnnotations(
            title="Take down HTML page",
            read_only_hint=False,
            destructive_hint=True,
            idempotent_hint=True,
            open_world_hint=True,
        )
    )
    async def unpublish_page(page_id: str) -> dict[str, object]:
        """Take down an HTML page you published: delete it from the page server so its link stops working.

        This cannot be undone; publishing the page again gives it a new link. Returns page_id, url and
        removed. Use list_pages to find the page_id.

        Args:
            page_id: The page_id of one of your pages, from publish_page or list_pages.
        """
        try:
            record = await app.service.unpublish(app.owner(), page_id)
        except PageError as error:
            raise ToolError(str(error)) from error
        return {"page_id": record.page_id, "url": app.service.url_for(record), "removed": True}

    uploads = app.uploads
    if uploads is not None:

        @server.tool(
            annotations=ToolAnnotations(
                title="Create page upload URL",
                read_only_hint=False,
                destructive_hint=False,
                idempotent_hint=False,
                open_world_hint=False,
            )
        )
        async def create_page_upload() -> dict[str, object]:
            """Create a one-time URL to upload a large HTML page to, for publish_page.

            Returns upload_id, upload_url, method (always PUT), max_bytes and expires_at. Send the file's
            bytes as the body of an HTTP PUT to upload_url within 15 minutes, for example
            `curl --upload-file page.html "<upload_url>"`, then call publish_page with upload_id and no html.
            Only useful when you can make HTTP requests or run commands (for example in Claude Code); otherwise
            pass the page to publish_page as html.
            """
            try:
                slot = await uploads.create(app.owner())
            except UploadError as error:
                raise ToolError(str(error)) from error
            return {
                "upload_id": slot.upload_id,
                "upload_url": slot.upload_url,
                "method": "PUT",
                "max_bytes": slot.max_bytes,
                "expires_at": _iso(slot.expires_at),
            }

    fetcher = app.fetcher
    if fetcher is not None:

        @server.tool(
            annotations=ToolAnnotations(
                title="Publish HTML page from URL",
                read_only_hint=False,
                destructive_hint=False,
                idempotent_hint=False,
                open_world_hint=True,
            )
        )
        async def publish_page_from_url(title: str, url: str, page_id: str = "") -> dict[str, object]:
            """Publish a single-file HTML page that is already online, by its https address, and return its link.

            The server downloads the file itself, so the page does not have to pass through this
            conversation; use it for large pages. The address must be on a host this server allows (an error
            names them) and must return the file to a plain GET with no sign-in, for example a raw file on a
            Git host or a presigned storage link. Returns page_id, url, title, bytes, created, updated_at
            and expires_at, as publish_page does. To change a page you published, pass its page_id; the page
            is replaced and keeps its url. For a page you have as text, use publish_page.

            Args:
                title: What the page is, in words, for example "Q3 report". 1 to 200 characters. It is
                    shown in list_pages only; the link is a random id and never contains it.
                url: The https address of the HTML file.
                page_id: The page_id of a page you published, to replace it. Empty to publish a new page.
            """
            owner = app.owner()
            try:
                await app.service.check_can_publish(owner, title, page_id)
                data = await fetcher.fetch(url)
                result = await app.service.publish(owner, title, data, page_id)
            except (PageError, FetchClientError) as error:
                raise ToolError(str(error)) from error
            return _publish_result(result)

    @server.tool()
    def server_info() -> dict[str, str]:
        """Report this server's name and version.

        Returns:
            A mapping with `name` and `version`; `version` is the installed package's version.
        """
        return {"name": SERVER_NAME, "version": __version__}

    return server
