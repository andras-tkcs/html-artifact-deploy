"""The real `serve-http` process over real Streamable HTTP, driven by the official MCP client.

An in-process test cannot see an entry point that never starts uvicorn, a startup crash, or a
secret leaking into the process's output; this can.
"""

from __future__ import annotations

import os
import socket
import subprocess  # nosec B404  # tests start this project's own server
import sys
import time
from pathlib import Path

import httpx2
import pytest
from mcp import Client
from mcp.client.streamable_http import streamable_http_client

pytestmark = pytest.mark.integration

SECRET_ENV = "HTML_ARTIFACT_DEPLOY_GOOGLE_CLIENT_SECRET"


def _free_port() -> int:
    with socket.socket() as sock:
        sock.bind(("127.0.0.1", 0))
        return int(sock.getsockname()[1])


def _write_config(tmp_path: Path, port: int) -> Path:
    pages = tmp_path / "pages"
    pages.mkdir()
    config = tmp_path / "config.toml"
    config.write_text(
        f'[pages]\nroot = "{pages}"\npublic_base_url = "https://pages.example.com"\n'
        f'[state]\ndir = "{tmp_path / "state"}"\n'
        f'[http]\npublic_url = "http://127.0.0.1:{port}"\nlisten_port = {port}\n'
        '[auth]\ngoogle_client_id = "x.apps.googleusercontent.com"\nallowed_domains = ["example.com"]\n',
        encoding="utf-8",
    )
    return config


def _wait_healthy(port: int, process: subprocess.Popen[str]) -> None:
    deadline = time.monotonic() + 20
    while time.monotonic() < deadline:
        if process.poll() is not None:
            raise AssertionError(f"serve-http exited early with {process.returncode}")
        try:
            if httpx2.get(f"http://127.0.0.1:{port}/healthz", timeout=2).status_code == 200:
                return
        except httpx2.TransportError:
            pass
        time.sleep(0.2)
    raise AssertionError("serve-http did not become healthy within 20 seconds")


class TestHttpContract:
    @pytest.mark.timeout(60)
    async def test_lists_tools_and_publishes_a_page(self, tmp_path: Path) -> None:
        port = _free_port()
        config = _write_config(tmp_path, port)
        base = [sys.executable, "-m", "html_artifact_deploy", "--config", str(config)]
        created = subprocess.run(  # nosec B603  # fixed argv
            [*base, "token", "create", "--user", "a@example.com", "--name", "t"],
            capture_output=True,
            text=True,
            check=True,
        )
        token = created.stdout.strip()
        assert token
        server = subprocess.Popen(  # nosec B603  # fixed argv
            [*base, "serve-http"],
            env={**os.environ, SECRET_ENV: "x"},
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
        )
        output = ""
        try:
            _wait_healthy(port, server)
            http_client = httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"})
            async with http_client:
                transport = streamable_http_client(f"http://127.0.0.1:{port}/mcp", http_client=http_client)
                async with Client(transport, read_timeout_seconds=20) as client:
                    names = {tool.name for tool in (await client.list_tools()).tools}
                    result = await client.call_tool("publish_page", {"title": "Hello", "html": "<p>hi</p>"})
        finally:
            server.terminate()
            try:
                output, _ = server.communicate(timeout=10)
            except subprocess.TimeoutExpired:  # pragma: no cover
                server.kill()
                output, _ = server.communicate()
        assert {"publish_page", "list_pages", "unpublish_page", "server_info"} <= names
        assert not result.is_error
        data = result.structured_content
        assert data is not None
        assert (tmp_path / "pages" / str(data["page_id"]) / "index.html").read_text() == "<p>hi</p>"
        assert token not in output
