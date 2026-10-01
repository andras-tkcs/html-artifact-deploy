"""html_artifact_deploy.__main__: argument handling, without starting a real transport."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from mcp.server.mcpserver import MCPServer

from html_artifact_deploy import __main__ as entry
from html_artifact_deploy import __version__
from html_artifact_deploy.config import HttpConfig
from html_artifact_deploy.server import AppContext

pytestmark = pytest.mark.unit


SECRET_ENV = "HTML_ARTIFACT_DEPLOY_GOOGLE_CLIENT_SECRET"
AUTH_SECTIONS = (
    '[http]\npublic_url = "https://publish.example.com"\n'
    '[auth]\ngoogle_client_id = "x.apps.googleusercontent.com"\nallowed_domains = ["example.com"]\n'
)


def _write_config(tmp_path: Path, *, pages: Path | None = None, auth: bool = False, fetch: bool = False) -> Path:
    root = pages if pages is not None else tmp_path / "pages"
    if pages is None:
        root.mkdir()
    path = tmp_path / "config.toml"
    path.write_text(
        f'[pages]\nroot = "{root}"\npublic_base_url = "https://pages.example.com"\n'
        f'[state]\ndir = "{tmp_path / "state"}"\n'
        + (AUTH_SECTIONS if auth else "")
        + ('[fetch]\nallowed_hosts = ["raw.example.com"]\n' if fetch else ""),
        encoding="utf-8",
    )
    return path


class TestMain:
    def test_version_flag_prints_version_and_exits(self, capsys: pytest.CaptureFixture[str]) -> None:
        with pytest.raises(SystemExit) as exc:
            entry.main(["--version"])
        assert exc.value.code == 0
        assert __version__ in capsys.readouterr().out

    def test_missing_config_file_is_an_error_on_stderr(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        missing = tmp_path / "nope.toml"
        assert entry.main(["--config", str(missing)], {}) == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == (
            f"html-artifact-deploy: {missing}: No configuration file here. "
            "Pass --config, or set HTML_ARTIFACT_DEPLOY_CONFIG.\n"
        )

    def test_missing_pages_folder_is_an_error(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        config = _write_config(tmp_path, pages=tmp_path / "absent")
        assert entry.main(["--config", str(config)], {}) == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "does not exist or this server cannot write to it" in captured.err

    def test_state_database_of_another_version_is_an_error(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        import sqlite3

        config = _write_config(tmp_path)
        (tmp_path / "state").mkdir()
        connection = sqlite3.connect(tmp_path / "state" / "state.sqlite3")
        connection.execute("PRAGMA user_version = 7")
        connection.close()
        assert entry.main(["--config", str(config)], {}) == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert "has schema version 7" in captured.err

    def test_runs_the_server_over_stdio(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        transports: list[str] = []
        monkeypatch.setattr(MCPServer, "run", lambda self, transport: transports.append(transport))
        assert entry.main(["--config", str(_write_config(tmp_path))], {}) == 0
        assert transports == ["stdio"]

    @pytest.mark.parametrize("fetch", [False, True])
    def test_stdio_passes_the_fetcher_only_with_a_fetch_section(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, fetch: bool
    ) -> None:
        contexts: list[AppContext] = []

        def fake_build_server(app: AppContext) -> Any:
            contexts.append(app)
            return type("S", (), {"run": lambda self, transport: None})()

        monkeypatch.setattr(entry, "build_server", fake_build_server)
        assert entry.main(["--config", str(_write_config(tmp_path, fetch=fetch))], {}) == 0
        assert (contexts[0].fetcher is not None) is fetch

    def test_environ_defaults_to_os_environ(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        transports: list[str] = []
        monkeypatch.setattr(MCPServer, "run", lambda self, transport: transports.append(transport))
        monkeypatch.setenv("HTML_ARTIFACT_DEPLOY_CONFIG", str(_write_config(tmp_path)))
        assert entry.main([]) == 0
        assert transports == ["stdio"]


class TestCheckConfig:
    def test_ok(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        config = _write_config(tmp_path)
        assert entry.main(["--config", str(config), "check-config"], {}) == 0
        assert capsys.readouterr().out == f"Configuration OK: {config}\n"

    def test_bad_file_is_2(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        bad = tmp_path / "bad.toml"
        bad.write_text("not toml [", encoding="utf-8")
        assert entry.main(["--config", str(bad), "check-config"], {}) == 2
        assert "not valid TOML" in capsys.readouterr().err

    def test_auth_with_the_secret_is_ok(self, tmp_path: Path) -> None:
        config = _write_config(tmp_path, auth=True)
        assert entry.main(["--config", str(config), "check-config"], {SECRET_ENV: "s"}) == 0

    def test_auth_without_the_secret_is_2(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        config = _write_config(tmp_path, auth=True)
        assert entry.main(["--config", str(config), "check-config"], {}) == 2
        assert SECRET_ENV in capsys.readouterr().err


class TestToken:
    def _run(self, config: Path, *args: str) -> int:
        return entry.main(["--config", str(config), "token", *args], {})

    def test_create_prints_only_the_token_on_stdout(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        config = _write_config(tmp_path, auth=True)
        assert self._run(config, "create", "--user", "A@Example.com", "--name", "t") == 0
        captured = capsys.readouterr()
        token = captured.out.strip()
        assert token.startswith("hdk_") and "\n" not in token
        assert captured.err == (
            f"Store this token now; it is not shown again. Use it as: Authorization: Bearer {token}\n"
        )

    def test_refused_address_is_2(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        config = _write_config(tmp_path, auth=True)
        assert self._run(config, "create", "--user", "a@other.org", "--name", "t") == 2
        captured = capsys.readouterr()
        assert captured.out == ""
        assert captured.err == (
            "html-artifact-deploy: a@other.org is not in auth.allowed_emails or auth.allowed_domains.\n"
        )

    def test_duplicate_name_is_2(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        config = _write_config(tmp_path, auth=True)
        assert self._run(config, "create", "--user", "a@example.com", "--name", "t") == 0
        capsys.readouterr()
        assert self._run(config, "create", "--user", "a@example.com", "--name", "t") == 2
        assert "a@example.com already has an API token named 't'." in capsys.readouterr().err

    def test_list_and_revoke(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        config = _write_config(tmp_path, auth=True)
        assert self._run(config, "create", "--user", "a@example.com", "--name", "t", "--days", "2") == 0
        capsys.readouterr()
        assert self._run(config, "list") == 0
        fields = capsys.readouterr().out.rstrip("\n").split("\t")
        assert fields[:2] == ["a@example.com", "t"]
        assert all(len(value) == 25 for value in fields[2:])  # YYYY-MM-DDTHH:MM:SS+00:00
        assert self._run(config, "revoke", "--user", "a@example.com", "--name", "t") == 0
        assert capsys.readouterr().out == "Revoked.\n"
        assert self._run(config, "revoke", "--user", "a@example.com", "--name", "t") == 1
        assert capsys.readouterr().err == "html-artifact-deploy: no such token.\n"

    def test_without_http_sections_is_2(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert self._run(_write_config(tmp_path), "list") == 2
        assert "needs the [http] and [auth] sections" in capsys.readouterr().err


class TestServeHttp:
    def test_runs_uvicorn_with_the_app_and_the_http_config(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        calls: list[tuple[Any, HttpConfig]] = []
        monkeypatch.setattr(entry, "_run_uvicorn", lambda app, http: calls.append((app, http)))
        config = _write_config(tmp_path, auth=True)
        assert entry.main(["--config", str(config), "serve-http"], {SECRET_ENV: "s"}) == 0
        assert len(calls) == 1
        assert calls[0][1] == HttpConfig(public_url="https://publish.example.com")

    def test_without_http_sections_is_2(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        assert entry.main(["--config", str(_write_config(tmp_path)), "serve-http"], {}) == 2
        assert "needs the [http] and [auth] sections" in capsys.readouterr().err

    def test_without_the_secret_is_2(self, tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
        config = _write_config(tmp_path, auth=True)
        assert entry.main(["--config", str(config), "serve-http"], {}) == 2
        assert SECRET_ENV in capsys.readouterr().err
