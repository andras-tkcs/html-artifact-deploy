"""html_artifact_deploy.__main__: argument handling, without starting a real transport."""

from __future__ import annotations

from pathlib import Path

import pytest
from mcp.server.mcpserver import MCPServer

from html_artifact_deploy import __main__ as entry
from html_artifact_deploy import __version__

pytestmark = pytest.mark.unit


def _write_config(tmp_path: Path, *, pages: Path | None = None) -> Path:
    root = pages if pages is not None else tmp_path / "pages"
    if pages is None:
        root.mkdir()
    path = tmp_path / "config.toml"
    path.write_text(
        f'[pages]\nroot = "{root}"\npublic_base_url = "https://pages.example.com"\n'
        f'[state]\ndir = "{tmp_path / "state"}"\n',
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

    def test_environ_defaults_to_os_environ(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        transports: list[str] = []
        monkeypatch.setattr(MCPServer, "run", lambda self, transport: transports.append(transport))
        monkeypatch.setenv("HTML_ARTIFACT_DEPLOY_CONFIG", str(_write_config(tmp_path)))
        assert entry.main([]) == 0
        assert transports == ["stdio"]
