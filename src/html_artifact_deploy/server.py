"""The MCP server: its tools, and the factory every transport and test builds it from.

`build_server()` returns a fresh `MCPServer` on every call instead of a module-level instance, so
tests never share state through it and `tests/conftest.py` has nothing to reset for it.
"""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer

from . import __version__

SERVER_NAME = "html-artifact-deploy"


def build_server() -> MCPServer:
    """Build the server with every tool registered."""
    server = MCPServer(SERVER_NAME)

    @server.tool()
    def echo(text: str) -> str:
        """Return `text` unchanged.

        A placeholder that proves the transport end to end. Replace it with the server's real
        tools, and delete it once one of them is covered by the same contract tests.

        Args:
            text: Any string. An empty string is returned as an empty string.

        Returns:
            The same string.
        """
        return text

    @server.tool()
    def server_info() -> dict[str, str]:
        """Report this server's name and version.

        Returns:
            A mapping with `name` and `version`; `version` is the installed package's version.
        """
        return {"name": SERVER_NAME, "version": __version__}

    return server
