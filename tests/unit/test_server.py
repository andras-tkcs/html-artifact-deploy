"""html_artifact_deploy.server: every tool, called in-process through the official MCP client.

In-process means the SDK dispatches without JSON-RPC framing; the real stdio transport is
tests/integration/test_stdio_contract.py's job.
"""

from __future__ import annotations

import pytest
from mcp import Client

from html_artifact_deploy import __version__
from html_artifact_deploy.server import SERVER_NAME, build_server

pytestmark = pytest.mark.unit


class TestToolList:
    async def test_lists_every_tool(self) -> None:
        async with Client(build_server()) as client:
            names = {tool.name for tool in (await client.list_tools()).tools}
        assert names == {"echo", "server_info"}

    async def test_every_tool_has_a_description(self) -> None:
        async with Client(build_server()) as client:
            tools = (await client.list_tools()).tools
        assert all(tool.description and tool.description.strip() for tool in tools)


class TestEcho:
    @pytest.mark.parametrize("text", ["hello", "", "ünïcödé"])
    async def test_returns_its_input(self, text: str) -> None:
        async with Client(build_server()) as client:
            result = await client.call_tool("echo", {"text": text})
        assert not result.is_error
        assert result.structured_content == {"result": text}


class TestServerInfo:
    async def test_reports_name_and_installed_version(self) -> None:
        async with Client(build_server()) as client:
            result = await client.call_tool("server_info", {})
        assert result.structured_content == {"name": SERVER_NAME, "version": __version__}


class TestFreshInstances:
    def test_each_call_builds_a_new_server(self) -> None:
        """Tests must never share a server through module state; see server.py's docstring."""
        assert build_server() is not build_server()
