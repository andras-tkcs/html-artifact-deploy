"""The real server process over the real stdio transport, driven by the official MCP client.

This is the contract an MCP host (Claude Desktop, Claude Code, any other client) relies on:
`python -m html_artifact_deploy` speaks MCP on stdin/stdout, lists its tools, and answers a call. An
in-process test cannot see a stray `print()` corrupting stdout, a crash at import time, or an
entry point that never starts the transport; this can.
"""

from __future__ import annotations

import sys

import pytest
from mcp import Client, StdioServerParameters

pytestmark = pytest.mark.integration


def _server_params() -> StdioServerParameters:
    return StdioServerParameters(command=sys.executable, args=["-m", "html_artifact_deploy"])


class TestStdioContract:
    async def test_lists_tools_and_answers_a_call(self) -> None:
        async with Client(_server_params(), read_timeout_seconds=20) as client:
            names = {tool.name for tool in (await client.list_tools()).tools}
            result = await client.call_tool("echo", {"text": "over stdio"})
        assert {"echo", "server_info"} <= names
        assert not result.is_error
        assert result.structured_content == {"result": "over stdio"}

    async def test_unknown_tool_is_an_error_not_a_crash(self) -> None:
        async with Client(_server_params(), read_timeout_seconds=20) as client:
            result = await client.call_tool("no_such_tool", {})
            still_alive = await client.list_tools()
        assert result.is_error
        assert still_alive.tools
