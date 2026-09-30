"""HTML Artifact Deploy: an MCP server."""

from __future__ import annotations

from importlib.metadata import PackageNotFoundError, version

try:
    __version__ = version("html-artifact-deploy")
except PackageNotFoundError:  # pragma: no cover - only in a checkout that was never pip-installed
    __version__ = "0.0.0.dev0"
