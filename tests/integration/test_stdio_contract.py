"""The real server process over the real stdio transport, driven by the official MCP client.

This is the contract an MCP host (Claude Desktop, Claude Code, any other client) relies on:
`python -m html_artifact_deploy` speaks MCP on stdin/stdout, lists its tools, and answers a call. An
in-process test cannot see a stray `print()` corrupting stdout, a crash at import time, or an
entry point that never starts the transport; this can.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters

pytestmark = pytest.mark.integration


def _server_params(tmp_path: Path) -> StdioServerParameters:
    pages = tmp_path / "pages"
    pages.mkdir()
    config = tmp_path / "config.toml"
    config.write_text(
        f'[pages]\nroot = "{pages}"\npublic_base_url = "https://pages.example.com"\n'
        f'[state]\ndir = "{tmp_path / "state"}"\n',
        encoding="utf-8",
    )
    return StdioServerParameters(command=sys.executable, args=["-m", "html_artifact_deploy", "--config", str(config)])


class TestStdioContract:
    async def test_lists_tools_and_publishes_a_page(self, tmp_path: Path) -> None:
        async with Client(_server_params(tmp_path), read_timeout_seconds=20) as client:
            names = {tool.name for tool in (await client.list_tools()).tools}
            result = await client.call_tool("publish_page", {"title": "Hello", "html": "<p>hi</p>"})
        assert {"publish_page", "list_pages", "unpublish_page", "server_info"} <= names
        assert not result.is_error
        data = result.structured_content
        assert data is not None
        assert str(data["url"]).startswith("https://pages.example.com/")
        assert (tmp_path / "pages" / str(data["page_id"]) / "index.html").read_text() == "<p>hi</p>"

    async def test_unknown_tool_is_an_error_not_a_crash(self, tmp_path: Path) -> None:
        async with Client(_server_params(tmp_path), read_timeout_seconds=20) as client:
            result = await client.call_tool("no_such_tool", {})
            still_alive = await client.list_tools()
        assert result.is_error
        assert still_alive.tools
