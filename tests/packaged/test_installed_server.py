"""The built wheel, installed into a clean environment, speaks MCP over stdio.

Layer 6 of docs/testing-policy.md. `build.yml`'s `smoke` job installs the wheel into a fresh venv
and runs this module with that venv's Python, which it also names in PACKAGED_PYTHON. Everywhere
else PACKAGED_PYTHON is unset and the module skips.

What it catches that the source-tree tests cannot: a module or data file missing from the wheel,
a dependency declared too loosely to resolve, an entry point that does not exist.
"""

from __future__ import annotations

import os
import subprocess  # nosec B404
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters

pytestmark = [
    pytest.mark.packaged,
    pytest.mark.skipif(not os.environ.get("PACKAGED_PYTHON"), reason="PACKAGED_PYTHON not set (build.yml only)"),
]

REPO_SRC = Path(__file__).resolve().parents[2] / "src"


def _python() -> str:
    return os.environ["PACKAGED_PYTHON"]


class TestInstalledWheel:
    def test_package_comes_from_the_install_not_the_checkout(self) -> None:
        out = subprocess.run(  # nosec B603
            [_python(), "-c", "import html_artifact_deploy; print(html_artifact_deploy.__file__)"],
            capture_output=True,
            text=True,
            check=True,
            cwd=os.path.expanduser("~"),
        ).stdout.strip()
        assert not Path(out).resolve().is_relative_to(REPO_SRC)

    async def test_entry_point_lists_tools_over_stdio(self, tmp_path: Path) -> None:
        pages = tmp_path / "pages"
        pages.mkdir()
        config = tmp_path / "config.toml"
        config.write_text(
            f'[pages]\nroot = "{pages}"\npublic_base_url = "https://pages.example.com"\n'
            f'[state]\ndir = "{tmp_path / "state"}"\n',
            encoding="utf-8",
        )
        params = StdioServerParameters(
            command=_python(),
            args=["-m", "html_artifact_deploy", "--config", str(config)],
            cwd=os.path.expanduser("~"),
        )
        async with Client(params, read_timeout_seconds=30) as client:
            tools = (await client.list_tools()).tools
            result = await client.call_tool("server_info", {})
        assert tools
        assert not result.is_error
