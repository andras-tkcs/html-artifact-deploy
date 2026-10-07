"""html_artifact_deploy.server: every tool, called in-process through the official MCP client.

In-process means the SDK dispatches without JSON-RPC framing; the real stdio transport is
tests/integration/test_stdio_contract.py's job.
"""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest
from mcp import Client
from mcp.types import CallToolResult, ToolAnnotations
from starlette.applications import Starlette
from starlette.routing import Route
from starlette.testclient import TestClient

from html_artifact_deploy import __version__
from html_artifact_deploy.config import FetchConfig, PagesConfig
from html_artifact_deploy.fetch_client import FetchClient
from html_artifact_deploy.page_index import PageIndex
from html_artifact_deploy.server import SERVER_NAME, AppContext, build_server
from html_artifact_deploy.service import PageService
from html_artifact_deploy.state import DB_FILE_NAME, StateDB
from html_artifact_deploy.storage import LocalFolderStore
from html_artifact_deploy.uploads import UPLOAD_PATH, UploadStore

from .test_fetch_client import FakeHTTPS, ok, public

pytestmark = pytest.mark.unit

BASE = "https://pages.example.com"
ALICE = "alice@example.com"
BOB = "bob@example.com"
HTML = "<!doctype html><title>Q3</title><p>héllo</p>"


DAY = 86400


class Env:
    def __init__(self, tmp_path: Path) -> None:
        self.root = tmp_path / "pages"
        self.root.mkdir()
        self.now = 1_700_000_000.0
        config = PagesConfig(root=self.root, public_base_url=BASE, max_expiry_days=200, default_expiry_days=30)
        index = PageIndex(StateDB(tmp_path / "state" / DB_FILE_NAME), clock=lambda: self.now)
        self.service = PageService(config, LocalFolderStore(self.root), index)
        self.uploads = UploadStore(StateDB(tmp_path / "state" / "up.sqlite3"), tmp_path / "uploads", BASE, 10_000)
        self.with_uploads = False
        self.fake: FakeHTTPS | None = None

    def server(self, owner: str = ALICE):  # type: ignore[no-untyped-def]
        uploads = self.uploads if self.with_uploads else None
        fetcher = None
        if self.fake is not None:
            config = FetchConfig(allowed_hosts=("raw.example.com",))
            fetcher = FetchClient(config, 10_000, resolve=public, handlers=[self.fake])
        return build_server(AppContext(self.service, owner=lambda: owner, uploads=uploads, fetcher=fetcher))

    def fetching(self, routes: dict[str, Any]) -> FakeHTTPS:
        self.fake = FakeHTTPS(routes)
        return self.fake

    async def upload(self, owner: str, body: bytes) -> str:
        """Create a slot for `owner` and PUT `body` to it through the real handler."""
        slot = await self.uploads.create(owner)
        app = Starlette(routes=[Route(UPLOAD_PATH, self.uploads.handle_put, methods=["PUT"])])
        with TestClient(app) as client:
            assert client.put(f"/uploads/{slot.upload_id}", content=body).status_code == 201
        return slot.upload_id


@pytest.fixture
def env(tmp_path: Path) -> Env:
    return Env(tmp_path)


async def _call(env: Env, tool: str, args: dict[str, object], owner: str = ALICE) -> CallToolResult:
    async with Client(env.server(owner)) as client:
        return await client.call_tool(tool, args)


def _text(result: CallToolResult) -> str:
    return "".join(getattr(block, "text", "") for block in result.content)


class TestToolList:
    async def test_lists_every_tool(self, env: Env) -> None:
        async with Client(env.server()) as client:
            names = {tool.name for tool in (await client.list_tools()).tools}
        assert names == {"publish_page", "list_pages", "unpublish_page", "extend_page_expiry", "server_info"}

    async def test_every_tool_has_a_description(self, env: Env) -> None:
        async with Client(env.server()) as client:
            tools = (await client.list_tools()).tools
        assert all(tool.description and tool.description.strip() for tool in tools)

    async def test_descriptions_carry_the_configured_limits(self, env: Env) -> None:
        env.fetching({})
        async with Client(env.server()) as client:
            tools = {tool.name: tool.description or "" for tool in (await client.list_tools()).tools}
        for name in ("publish_page", "publish_page_from_url", "extend_page_expiry"):
            assert "200" in tools[name], name
        for name in ("publish_page", "publish_page_from_url"):
            assert "30 days for a new page" in tools[name], name
            assert "Pages expire: an expired page is deleted" in tools[name], name
        assert "Change when one of your HTML pages expires" in tools["extend_page_expiry"]

    async def test_no_description_has_a_brace(self, env: Env) -> None:
        env.with_uploads = True
        env.fetching({})
        async with Client(env.server()) as client:
            tools = (await client.list_tools()).tools
        assert len(tools) == 7
        for tool in tools:
            assert "{" not in (tool.description or "") and "}" not in (tool.description or ""), tool.name

    async def test_annotations(self, env: Env) -> None:
        async with Client(env.server()) as client:
            tools = {tool.name: tool.annotations for tool in (await client.list_tools()).tools}
        assert tools["publish_page"] == ToolAnnotations(
            title="Publish HTML page",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        )
        assert tools["list_pages"] == ToolAnnotations(
            title="List my HTML pages",
            readOnlyHint=True,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        )
        assert tools["extend_page_expiry"] == ToolAnnotations(
            title="Change HTML page expiry",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=True,
            openWorldHint=False,
        )
        assert tools["unpublish_page"] == ToolAnnotations(
            title="Take down HTML page",
            readOnlyHint=False,
            destructiveHint=True,
            idempotentHint=True,
            openWorldHint=True,
        )


class TestPublishPage:
    async def test_publishes_a_new_page(self, env: Env) -> None:
        result = await _call(env, "publish_page", {"title": "Secret Q3 title", "html": HTML})
        assert not result.is_error
        data = result.structured_content
        assert data is not None
        page_id = data["page_id"]
        assert data["url"] == f"https://pages.example.com/{page_id}/"
        assert "Secret" not in str(data["url"])
        assert data["title"] == "Secret Q3 title"
        assert data["bytes"] == len(HTML.encode())
        assert data["created"] is True
        assert str(data["updated_at"]).endswith("+00:00")
        assert str(data["expires_at"]).endswith("+00:00")
        assert (env.root / str(page_id) / "index.html").read_text(encoding="utf-8") == HTML

    async def test_replaces_a_page_at_the_same_url(self, env: Env) -> None:
        first = (await _call(env, "publish_page", {"title": "v1", "html": HTML})).structured_content
        assert first is not None
        second = (
            await _call(env, "publish_page", {"title": "v2", "html": "<p>v2</p>", "page_id": first["page_id"]})
        ).structured_content
        assert second is not None
        assert second["created"] is False
        assert second["url"] == first["url"]
        assert second["expires_at"] == first["expires_at"]
        assert (env.root / str(first["page_id"]) / "index.html").read_text() == "<p>v2</p>"

    @pytest.mark.parametrize(
        ("args", "message"),
        [
            ({"title": "t", "html": HTML, "upload_id": "u"}, "Give exactly one of html and upload_id."),
            ({"title": "t"}, "Give exactly one of html and upload_id."),
            (
                {"title": "t", "upload_id": "u"},
                "Uploads are not available on this connection; pass the page as html.",
            ),
            (
                {"title": "t", "html": HTML, "page_id": "a" * 32},
                f"No page with page_id {'a' * 32!r} was published by you, or it has expired. "
                "Call list_pages to see your pages.",
            ),
            ({"title": "t" * 201, "html": HTML}, "title must be 1 to 200 characters."),
        ],
    )
    async def test_errors(self, env: Env, args: dict[str, object], message: str) -> None:
        result = await _call(env, "publish_page", args)
        assert result.is_error
        assert _text(result) == f"Error executing tool publish_page: {message}"


PAGE_URL = "https://raw.example.com/org/repo/page.html"


class TestPublishPageFromUrl:
    async def test_absent_without_a_fetcher(self, env: Env) -> None:
        async with Client(env.server()) as client:
            names = {tool.name for tool in (await client.list_tools()).tools}
        assert "publish_page_from_url" not in names

    async def test_listed_with_its_annotations(self, env: Env) -> None:
        env.fetching({})
        async with Client(env.server()) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        assert tools["publish_page_from_url"].annotations == ToolAnnotations(
            title="Publish HTML page from URL",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=True,
        )
        assert tools["publish_page_from_url"].description

    async def test_publishes_the_downloaded_file(self, env: Env) -> None:
        fake = env.fetching({PAGE_URL: ok(HTML.encode())})
        result = await _call(env, "publish_page_from_url", {"title": "Q3", "url": PAGE_URL})
        assert not result.is_error
        data = result.structured_content
        assert data is not None
        assert data["created"] is True
        assert data["bytes"] == len(HTML.encode())
        assert data["url"] == f"{BASE}/{data['page_id']}/"
        assert (env.root / str(data["page_id"]) / "index.html").read_text(encoding="utf-8") == HTML
        assert len(fake.requests) == 1

    async def test_page_id_replaces_and_keeps_the_url(self, env: Env) -> None:
        env.fetching({PAGE_URL: ok(b"<p>v2</p>")})
        first = (await _call(env, "publish_page", {"title": "v1", "html": HTML})).structured_content
        assert first is not None
        second = (
            await _call(env, "publish_page_from_url", {"title": "v2", "url": PAGE_URL, "page_id": first["page_id"]})
        ).structured_content
        assert second is not None
        assert second["created"] is False
        assert second["url"] == first["url"]
        assert (env.root / str(first["page_id"]) / "index.html").read_text() == "<p>v2</p>"

    async def test_bad_title_costs_no_download(self, env: Env) -> None:
        fake = env.fetching({PAGE_URL: ok()})
        result = await _call(env, "publish_page_from_url", {"title": "t" * 201, "url": PAGE_URL})
        assert result.is_error
        assert _text(result) == "Error executing tool publish_page_from_url: title must be 1 to 200 characters."
        assert fake.requests == []

    async def test_foreign_page_id_costs_no_download(self, env: Env) -> None:
        fake = env.fetching({PAGE_URL: ok()})
        bobs = (await _call(env, "publish_page", {"title": "b", "html": HTML}, owner=BOB)).structured_content
        assert bobs is not None
        result = await _call(env, "publish_page_from_url", {"title": "a", "url": PAGE_URL, "page_id": bobs["page_id"]})
        assert result.is_error
        assert "was published by you, or it has expired" in _text(result)
        assert fake.requests == []

    async def test_fetch_error_is_the_tool_error(self, env: Env) -> None:
        env.fetching({})
        result = await _call(env, "publish_page_from_url", {"title": "t", "url": "http://raw.example.com/x"})
        assert result.is_error
        assert _text(result).startswith("Error executing tool publish_page_from_url: url must be an https:// address")

    async def test_body_that_is_not_utf8(self, env: Env) -> None:
        env.fetching({PAGE_URL: ok(b"\xff\xfe<p>")})
        result = await _call(env, "publish_page_from_url", {"title": "t", "url": PAGE_URL})
        assert result.is_error
        assert _text(result).startswith("Error executing tool publish_page_from_url: ")
        assert "UTF-8" in _text(result)


class TestUploads:
    async def test_tool_is_listed_with_its_annotations(self, env: Env) -> None:
        env.with_uploads = True
        async with Client(env.server()) as client:
            tools = {tool.name: tool for tool in (await client.list_tools()).tools}
        assert tools["create_page_upload"].annotations == ToolAnnotations(
            title="Create page upload URL",
            readOnlyHint=False,
            destructiveHint=False,
            idempotentHint=False,
            openWorldHint=False,
        )
        assert tools["create_page_upload"].description

    async def test_create_page_upload_result(self, env: Env) -> None:
        env.with_uploads = True
        result = await _call(env, "create_page_upload", {})
        data = result.structured_content
        assert data is not None
        assert data["upload_url"] == f"{BASE}/uploads/{data['upload_id']}"
        assert (data["method"], data["max_bytes"]) == ("PUT", 10_000)
        assert str(data["expires_at"]).endswith("+00:00")

    async def test_create_page_upload_reports_the_open_slot_limit(self, env: Env) -> None:
        env.with_uploads = True
        for _ in range(10):
            await _call(env, "create_page_upload", {})
        result = await _call(env, "create_page_upload", {})
        assert result.is_error
        assert "You have 10 uploads waiting." in _text(result)

    async def test_publish_via_upload_id_discards_the_upload(self, env: Env) -> None:
        env.with_uploads = True
        upload_id = await env.upload(ALICE, HTML.encode())
        result = await _call(env, "publish_page", {"title": "Big", "upload_id": upload_id})
        data = result.structured_content
        assert data is not None
        assert (env.root / str(data["page_id"]) / "index.html").read_text(encoding="utf-8") == HTML
        again = await _call(env, "publish_page", {"title": "Big", "upload_id": upload_id})
        assert again.is_error
        assert "No uploaded file for that upload_id." in _text(again)

    async def test_bad_title_does_not_consume_the_upload(self, env: Env) -> None:
        env.with_uploads = True
        upload_id = await env.upload(ALICE, HTML.encode())
        bad = await _call(env, "publish_page", {"title": "", "upload_id": upload_id})
        assert "title must be 1 to 200 characters." in _text(bad)
        good = await _call(env, "publish_page", {"title": "ok", "upload_id": upload_id})
        assert not good.is_error

    async def test_failed_publish_keeps_the_upload(self, env: Env) -> None:
        env.with_uploads = True
        upload_id = await env.upload(ALICE, b"\xff\xfe")
        bad = await _call(env, "publish_page", {"title": "t", "upload_id": upload_id})
        assert "not UTF-8" in _text(bad)
        assert await env.uploads.read(ALICE, upload_id) == b"\xff\xfe"

    async def test_without_a_store_the_tool_is_absent_and_upload_id_is_refused(self, env: Env) -> None:
        async with Client(env.server()) as client:
            names = {tool.name for tool in (await client.list_tools()).tools}
        assert "create_page_upload" not in names
        result = await _call(env, "publish_page", {"title": "t", "upload_id": "u"})
        assert "Uploads are not available on this connection" in _text(result)


class TestListPages:
    async def test_lists_own_pages(self, env: Env) -> None:
        published = (await _call(env, "publish_page", {"title": "One", "html": HTML})).structured_content
        assert published is not None
        result = await _call(env, "list_pages", {})
        data = result.structured_content
        assert data is not None
        assert data["count"] == 1
        (page,) = data["pages"]  # type: ignore[misc]
        assert page["page_id"] == published["page_id"]
        assert page["title"] == "One"
        assert page["url"] == published["url"]
        assert set(page) == {"page_id", "title", "url", "bytes", "created_at", "updated_at", "expires_at"}

    async def test_empty(self, env: Env) -> None:
        data = (await _call(env, "list_pages", {"limit": 5})).structured_content
        assert data == {"pages": [], "count": 0}


class TestUnpublishPage:
    async def test_removes_the_page(self, env: Env) -> None:
        published = (await _call(env, "publish_page", {"title": "One", "html": HTML})).structured_content
        assert published is not None
        result = await _call(env, "unpublish_page", {"page_id": published["page_id"]})
        assert result.structured_content == {"page_id": published["page_id"], "url": published["url"], "removed": True}
        assert not (env.root / str(published["page_id"])).exists()

    async def test_unknown_page(self, env: Env) -> None:
        result = await _call(env, "unpublish_page", {"page_id": "b" * 32})
        assert result.is_error
        assert _text(result) == (
            f"Error executing tool unpublish_page: No page with page_id {'b' * 32!r} was published by you, or it has expired. "
            "Call list_pages to see your pages."
        )


class TestOwnership:
    async def test_a_second_owner_cannot_see_or_remove_a_page(self, env: Env) -> None:
        published = (await _call(env, "publish_page", {"title": "Mine", "html": HTML})).structured_content
        assert published is not None
        page_id = published["page_id"]
        listed = (await _call(env, "list_pages", {}, owner=BOB)).structured_content
        assert listed == {"pages": [], "count": 0}
        removed = await _call(env, "unpublish_page", {"page_id": page_id}, owner=BOB)
        assert removed.is_error
        replaced = await _call(env, "publish_page", {"title": "x", "html": HTML, "page_id": page_id}, owner=BOB)
        assert replaced.is_error
        assert (env.root / str(page_id) / "index.html").exists()


class TestServerInfo:
    async def test_reports_name_and_installed_version(self, env: Env) -> None:
        result = await _call(env, "server_info", {})
        assert result.structured_content == {"name": SERVER_NAME, "version": __version__}


class TestFreshInstances:
    def test_each_call_builds_a_new_server(self, env: Env) -> None:
        """Tests must never share a server through module state; see server.py's docstring."""
        build: Callable[[], object] = env.server
        assert build() is not build()


async def _publish(env: Env, **extra: object) -> dict[str, object]:
    result = await _call(env, "publish_page", {"title": "One", "html": HTML, **extra})
    assert not result.is_error, _text(result)
    assert result.structured_content is not None
    return result.structured_content


class TestExpiry:
    async def test_default_and_explicit_expiry(self, env: Env) -> None:
        default = await _publish(env)
        assert str(default["expires_at"]).startswith("2023-12-")  # 30 days after 2023-11-14
        explicit = await _publish(env, expires_in_days=1)
        assert explicit["expires_at"] != default["expires_at"]

    async def test_bad_expires_in_days(self, env: Env) -> None:
        result = await _call(env, "publish_page", {"title": "t", "html": HTML, "expires_in_days": 201})
        assert result.is_error
        assert "expires_in_days must be from 1 to 200, or 0 for the default of 30." in _text(result)

    async def test_listed_with_expires_at(self, env: Env) -> None:
        published = await _publish(env, expires_in_days=5)
        (page,) = (await _call(env, "list_pages", {})).structured_content["pages"]  # type: ignore[index]
        assert page["expires_at"] == published["expires_at"]

    async def test_extend(self, env: Env) -> None:
        published = await _publish(env)
        result = await _call(env, "extend_page_expiry", {"page_id": published["page_id"], "days": 200})
        assert not result.is_error
        data = result.structured_content
        assert data is not None
        assert set(data) == {"page_id", "url", "expires_at"}
        assert data["url"] == published["url"]
        assert data["expires_at"] != published["expires_at"]

    async def test_extend_beyond_the_limit_and_unknown_page(self, env: Env) -> None:
        published = await _publish(env)
        too_long = await _call(env, "extend_page_expiry", {"page_id": published["page_id"], "days": 201})
        assert _text(too_long) == "Error executing tool extend_page_expiry: days must be from 1 to 200."
        other = await _call(env, "extend_page_expiry", {"page_id": published["page_id"], "days": 5}, owner=BOB)
        assert "No page with page_id" in _text(other)

    async def test_expired_page_is_gone_then_purged_by_the_next_publish(self, env: Env) -> None:
        published = await _publish(env, expires_in_days=1)
        page_id = str(published["page_id"])
        env.now += 2 * DAY
        assert (await _call(env, "list_pages", {})).structured_content == {"pages": [], "count": 0}
        extended = await _call(env, "extend_page_expiry", {"page_id": page_id, "days": 5})
        assert "No page with page_id" in _text(extended)
        unpublished = await _call(env, "unpublish_page", {"page_id": page_id})
        assert "No page with page_id" not in _text(unpublished)  # get_any still finds it: take-down works
        # an expired page whose owner did not take it down is purged by the next publish
        second = await _publish(env, expires_in_days=1)
        env.now += 2 * DAY
        await _publish(env)
        assert not (env.root / str(second["page_id"])).exists()

    async def test_expired_page_cannot_be_unpublished_as_unknown_after_purge(self, env: Env) -> None:
        published = await _publish(env, expires_in_days=1)
        env.now += 2 * DAY
        await _publish(env)  # purges it
        result = await _call(env, "unpublish_page", {"page_id": published["page_id"]})
        assert "No page with page_id" in _text(result)

    async def test_from_url_accepts_expires_in_days(self, env: Env) -> None:
        env.fetching({PAGE_URL: ok(HTML.encode())})
        result = await _call(env, "publish_page_from_url", {"title": "Q3", "url": PAGE_URL, "expires_in_days": 200})
        assert not result.is_error
        bad = await _call(env, "publish_page_from_url", {"title": "Q3", "url": PAGE_URL, "expires_in_days": 201})
        assert "expires_in_days must be from 1 to 200" in _text(bad)

    async def test_upload_path_accepts_expires_in_days(self, env: Env) -> None:
        env.with_uploads = True
        upload_id = await env.upload(ALICE, HTML.encode())
        ok_result = await _call(env, "publish_page", {"title": "t", "upload_id": upload_id, "expires_in_days": 7})
        assert not ok_result.is_error
        upload_id = await env.upload(ALICE, HTML.encode())
        bad = await _call(env, "publish_page", {"title": "t", "upload_id": upload_id, "expires_in_days": 999})
        assert "expires_in_days must be from 1 to 200" in _text(bad)
