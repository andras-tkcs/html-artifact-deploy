"""The deployment files and guides carry what the server needs: example files and docs stay in step."""

from __future__ import annotations

import tomllib
from pathlib import Path

import pytest

pytestmark = pytest.mark.unit

REPO_ROOT = Path(__file__).resolve().parents[2]
DEPLOY = REPO_ROOT / "deploy"
DOCS = REPO_ROOT / "docs"

CSP = (
    'Content-Security-Policy "sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox '
    'allow-forms allow-modals allow-downloads"'
)


def _keys(table: dict[str, object]) -> list[str]:
    return [key for value in table.values() if isinstance(value, dict) for key in value]


def test_every_example_config_key_is_documented() -> None:
    example = tomllib.loads((DEPLOY / "config.example.toml").read_text(encoding="utf-8"))
    text = (DOCS / "configuration.md").read_text(encoding="utf-8")
    keys = _keys(example)
    assert keys
    missing = [key for key in keys if f"`{key}`" not in text]
    assert missing == []


def test_caddyfile_pages_site_sandboxes_and_lists_nothing() -> None:
    text = (DEPLOY / "Caddyfile.example").read_text(encoding="utf-8")
    pages_site = text[text.index("pages.example.com") :]
    assert CSP in pages_site
    assert "browse" not in text


def test_service_unit_runs_serve_http_with_hardening() -> None:
    text = (DEPLOY / "html-artifact-deploy.service").read_text(encoding="utf-8")
    exec_start = next(line for line in text.splitlines() if line.startswith("ExecStart="))
    assert "serve-http" in exec_start
    for line in ("EnvironmentFile=", "ReadWritePaths=/srv/pages", "NoNewPrivileges=yes"):
        assert line in text


def test_deployment_guide_names_the_features_it_explains() -> None:
    text = (DOCS / "deployment.md").read_text(encoding="utf-8")
    for needle in (
        "create_page_upload",
        "publish_page_from_url",
        "fetch.allowed_hosts",
        "extend_page_expiry",
        "html-artifact-deploy-purge.timer",
    ):
        assert needle in text


def test_purge_timer_is_hourly_and_service_is_oneshot() -> None:
    timer = (DEPLOY / "html-artifact-deploy-purge.timer").read_text(encoding="utf-8")
    service = (DEPLOY / "html-artifact-deploy-purge.service").read_text(encoding="utf-8")
    assert "OnCalendar=hourly" in timer
    assert "Type=oneshot" in service
    exec_start = next(line for line in service.splitlines() if line.startswith("ExecStart="))
    assert "purge-expired" in exec_start
