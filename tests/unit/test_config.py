"""html_artifact_deploy.config: the TOML file, its checks, and the sign-in rules built on it."""

from __future__ import annotations

from pathlib import Path

import pytest

from html_artifact_deploy.config import (
    CONFIG_ENV,
    DEFAULT_CONFIG_PATH,
    DEFAULT_REDIRECT_URI_ALLOWLIST,
    DEFAULT_SECRET_ENV,
    AuthConfig,
    Config,
    ConfigError,
    HttpConfig,
    PagesConfig,
    google_client_secret,
    is_allowed_email,
    load_config,
    require_http,
    resolve_config_path,
)

pytestmark = pytest.mark.unit

MINIMAL = """
[pages]
root = "/srv/pages"
public_base_url = "https://pages.example.com"

[state]
dir = "/var/lib/had"
"""

AUTH = """
[http]
public_url = "https://publish.example.com/"

[auth]
google_client_id = "1-a.apps.googleusercontent.com"
allowed_domains = ["Example.com"]
allowed_emails = ["Guest@Other.org"]
"""

EXAMPLE = Path(__file__).resolve().parents[2] / "deploy" / "config.example.toml"


def write(tmp_path: Path, text: str) -> Path:
    path = tmp_path / "config.toml"
    path.write_text(text, encoding="utf-8")
    return path


def load(tmp_path: Path, text: str) -> Config:
    return load_config(write(tmp_path, text), {})


class TestLoadConfig:
    def test_full_file(self, tmp_path: Path) -> None:
        text = """
[pages]
root = "/srv/pages"
public_base_url = "https://pages.example.com/"
max_bytes = 1000
default_expiry_days = 10
max_expiry_days = 20

[state]
dir = "/var/lib/had"

[stdio]
user = "me"

[http]
public_url = "https://publish.example.com/"
listen_host = "0.0.0.0"
listen_port = 9000

[auth]
google_client_id = "1-a.apps.googleusercontent.com"
google_client_secret_env = "MY_SECRET"
allowed_domains = ["Example.com"]
allowed_emails = ["Guest@Other.org"]
redirect_uri_allowlist = ["https://app.example.com/cb"]
"""
        path = write(tmp_path, text)
        assert load_config(path, {}) == Config(
            path=path,
            pages=PagesConfig(Path("/srv/pages"), "https://pages.example.com", 1000, 10, 20),
            state_dir=Path("/var/lib/had"),
            stdio_user="me",
            http=HttpConfig("https://publish.example.com", "0.0.0.0", 9000),
            auth=AuthConfig(
                "1-a.apps.googleusercontent.com",
                ("example.com",),
                ("guest@other.org",),
                "MY_SECRET",
                ("https://app.example.com/cb",),
            ),
        )

    def test_minimal_file_uses_defaults(self, tmp_path: Path) -> None:
        config = load(tmp_path, MINIMAL)
        assert config.http is None
        assert config.auth is None
        assert config.stdio_user == "local"
        assert config.pages.max_bytes == 16_000_000
        assert config.pages.default_expiry_days == 90
        assert config.pages.max_expiry_days == 365

    def test_http_and_auth_defaults(self, tmp_path: Path) -> None:
        config = load(tmp_path, MINIMAL + AUTH.replace("pages.example", "x.example"))
        assert config.http == HttpConfig("https://publish.example.com")
        assert config.auth is not None
        assert config.auth.google_client_secret_env == DEFAULT_SECRET_ENV
        assert config.auth.redirect_uri_allowlist == DEFAULT_REDIRECT_URI_ALLOWLIST

    def test_lower_max_expiry_needs_a_lower_default(self, tmp_path: Path) -> None:
        with pytest.raises(ConfigError, match=r"pages.default_expiry_days must be .* \(30\)"):
            load(
                tmp_path,
                MINIMAL.replace('"https://pages.example.com"', '"https://pages.example.com"\nmax_expiry_days = 30'),
            )
        config = load(
            tmp_path,
            MINIMAL.replace(
                '"https://pages.example.com"',
                '"https://pages.example.com"\nmax_expiry_days = 30\ndefault_expiry_days = 30',
            ),
        )
        assert config.pages.default_expiry_days == 30

    def test_local_http_urls_are_allowed(self, tmp_path: Path) -> None:
        text = MINIMAL.replace("https://pages.example.com", "http://localhost:8080") + AUTH.replace(
            "https://publish.example.com/", "http://127.0.0.1:8765"
        )
        config = load(tmp_path, text)
        assert config.pages.public_base_url == "http://localhost:8080"

    def test_example_file_loads_with_an_empty_environ(self) -> None:
        config = load_config(EXAMPLE, {})
        assert config.http is not None
        assert config.auth is not None


def _replace(text: str, old: str, new: str) -> str:
    assert old in text
    return text.replace(old, new)


FULL = MINIMAL + AUTH
ROOT = 'root = "/srv/pages"'
BASE = 'public_base_url = "https://pages.example.com"'
STATE_DIR = 'dir = "/var/lib/had"'
PUBLIC = 'public_url = "https://publish.example.com/"'
CLIENT_ID = 'google_client_id = "1-a.apps.googleusercontent.com"'
DOMAINS = 'allowed_domains = ["Example.com"]'
EMAILS = 'allowed_emails = ["Guest@Other.org"]'

ROOT_MSG = "pages.root must be the absolute path of the folder the web server publishes, for example /srv/pages"
BASE_MSG = (
    "pages.public_base_url must be the https:// address at which the web server publishes pages.root, "
    "for example https://pages.example.com"
)
STATE_MSG = (
    "state.dir must be the absolute path of a folder this server keeps its database in, "
    "for example /var/lib/html-artifact-deploy"
)
PUBLIC_MSG = "http.public_url must be the https:// address of this server, for example https://publish.example.com"
BYTES_MSG = "pages.max_bytes must be a whole number of bytes from 1 to 50000000"
MAX_EXPIRY_MSG = "pages.max_expiry_days must be a whole number of days from 1 to 365"
DEFAULT_EXPIRY_MSG = "pages.default_expiry_days must be a whole number of days from 1 to pages.max_expiry_days (365)"
CLIENT_ID_MSG = (
    "auth.google_client_id must be the Client ID of a Google OAuth client, ending in .apps.googleusercontent.com"
)
SECRET_ENV_MSG = (
    "auth.google_client_secret_env must be the name of an environment variable, "
    "for example HTML_ARTIFACT_DEPLOY_GOOGLE_CLIENT_SECRET"
)
REDIRECT_MSG = "auth.redirect_uri_allowlist must be a list of https:// redirect URIs"

BAD_FILES: list[tuple[str, str, str]] = [
    ("not-toml", "[pages\n", "not valid TOML: "),
    ("unknown-section", MINIMAL + "[extra]\na = 1\n", "unknown section [extra]"),
    ("fetch-is-unknown-until-p8", MINIMAL + '[fetch]\nallowed_hosts = ["a.example.com"]\n', "unknown section [fetch]"),
    ("section-not-a-table", "stdio = 3\n" + MINIMAL, "[stdio] must be a table"),
    ("unknown-pages-key", _replace(MINIMAL, ROOT, ROOT + "\nrot = 1"), "unknown key pages.rot"),
    ("unknown-auth-key", _replace(FULL, DOMAINS, DOMAINS + "\nallowed_domain = []"), "unknown key auth.allowed_domain"),
    ("pages-missing", '[state]\ndir = "/x"\n', "the [pages] section is required"),
    ("root-missing", _replace(MINIMAL, ROOT, ""), ROOT_MSG),
    ("root-relative", _replace(MINIMAL, ROOT, 'root = "pages"'), ROOT_MSG),
    ("root-empty", _replace(MINIMAL, ROOT, 'root = ""'), ROOT_MSG),
    ("root-not-string", _replace(MINIMAL, ROOT, "root = 5"), ROOT_MSG),
    ("base-missing", _replace(MINIMAL, BASE, ""), BASE_MSG),
    ("base-not-string", _replace(MINIMAL, BASE, "public_base_url = 5"), BASE_MSG),
    ("base-http", _replace(MINIMAL, BASE, 'public_base_url = "http://pages.example.com"'), BASE_MSG),
    ("base-no-host", _replace(MINIMAL, BASE, 'public_base_url = "https://"'), BASE_MSG),
    ("base-query", _replace(MINIMAL, BASE, 'public_base_url = "https://pages.example.com/?a=1"'), BASE_MSG),
    ("base-fragment", _replace(MINIMAL, BASE, 'public_base_url = "https://pages.example.com/#a"'), BASE_MSG),
    ("base-user-info", _replace(MINIMAL, BASE, 'public_base_url = "https://me@pages.example.com"'), BASE_MSG),
    ("base-unparseable", _replace(MINIMAL, BASE, 'public_base_url = "https://["'), BASE_MSG),
    ("max-bytes-zero", _replace(MINIMAL, BASE, BASE + "\nmax_bytes = 0"), BYTES_MSG),
    ("max-bytes-too-big", _replace(MINIMAL, BASE, BASE + "\nmax_bytes = 50000001"), BYTES_MSG),
    ("max-bytes-bool", _replace(MINIMAL, BASE, BASE + "\nmax_bytes = true"), BYTES_MSG),
    ("max-bytes-string", _replace(MINIMAL, BASE, BASE + '\nmax_bytes = "9"'), BYTES_MSG),
    ("max-expiry-zero", _replace(MINIMAL, BASE, BASE + "\nmax_expiry_days = 0"), MAX_EXPIRY_MSG),
    ("max-expiry-366", _replace(MINIMAL, BASE, BASE + "\nmax_expiry_days = 366"), MAX_EXPIRY_MSG),
    ("max-expiry-bool", _replace(MINIMAL, BASE, BASE + "\nmax_expiry_days = true"), MAX_EXPIRY_MSG),
    ("default-expiry-zero", _replace(MINIMAL, BASE, BASE + "\ndefault_expiry_days = 0"), DEFAULT_EXPIRY_MSG),
    ("default-expiry-over-max", _replace(MINIMAL, BASE, BASE + "\ndefault_expiry_days = 366"), DEFAULT_EXPIRY_MSG),
    ("default-expiry-bool", _replace(MINIMAL, BASE, BASE + "\ndefault_expiry_days = true"), DEFAULT_EXPIRY_MSG),
    ("state-missing", _replace(MINIMAL, "[state]\n" + STATE_DIR, ""), STATE_MSG),
    ("state-dir-missing", _replace(MINIMAL, STATE_DIR, ""), STATE_MSG),
    ("state-dir-relative", _replace(MINIMAL, STATE_DIR, 'dir = "state"'), STATE_MSG),
    ("state-dir-empty", _replace(MINIMAL, STATE_DIR, 'dir = ""'), STATE_MSG),
    ("state-dir-not-string", _replace(MINIMAL, STATE_DIR, "dir = 5"), STATE_MSG),
    (
        "stdio-user-empty",
        MINIMAL + '[stdio]\nuser = ""\n',
        "stdio.user must be a non-empty name, at most 200 characters",
    ),
    (
        "stdio-user-long",
        MINIMAL + f'[stdio]\nuser = "{"x" * 201}"\n',
        "stdio.user must be a non-empty name, at most 200 characters",
    ),
    (
        "stdio-user-not-string",
        MINIMAL + "[stdio]\nuser = 4\n",
        "stdio.user must be a non-empty name, at most 200 characters",
    ),
    ("http-public-missing", _replace(FULL, PUBLIC, ""), PUBLIC_MSG),
    ("http-public-http", _replace(FULL, PUBLIC, 'public_url = "http://publish.example.com"'), PUBLIC_MSG),
    (
        "http-host-empty",
        _replace(FULL, PUBLIC, PUBLIC + '\nlisten_host = ""'),
        "http.listen_host must be an address to listen on, for example 127.0.0.1",
    ),
    (
        "http-host-not-string",
        _replace(FULL, PUBLIC, PUBLIC + "\nlisten_host = 1"),
        "http.listen_host must be an address to listen on, for example 127.0.0.1",
    ),
    (
        "http-port-zero",
        _replace(FULL, PUBLIC, PUBLIC + "\nlisten_port = 0"),
        "http.listen_port must be a whole number from 1 to 65535",
    ),
    (
        "http-port-big",
        _replace(FULL, PUBLIC, PUBLIC + "\nlisten_port = 65536"),
        "http.listen_port must be a whole number from 1 to 65535",
    ),
    (
        "http-port-bool",
        _replace(FULL, PUBLIC, PUBLIC + "\nlisten_port = true"),
        "http.listen_port must be a whole number from 1 to 65535",
    ),
    (
        "same-host",
        _replace(FULL, PUBLIC, 'public_url = "https://pages.example.com:8443"'),
        "pages.public_base_url must be on a different host name from http.public_url: "
        "a published page runs its own scripts, and must not share an origin with this server",
    ),
    ("client-id-missing", _replace(FULL, CLIENT_ID, ""), CLIENT_ID_MSG),
    ("client-id-wrong-suffix", _replace(FULL, CLIENT_ID, 'google_client_id = "1-a.example.com"'), CLIENT_ID_MSG),
    ("client-id-not-string", _replace(FULL, CLIENT_ID, "google_client_id = 7"), CLIENT_ID_MSG),
    (
        "secret-env-lowercase",
        _replace(FULL, CLIENT_ID, CLIENT_ID + '\ngoogle_client_secret_env = "my_secret"'),
        SECRET_ENV_MSG,
    ),
    (
        "secret-env-digit-first",
        _replace(FULL, CLIENT_ID, CLIENT_ID + '\ngoogle_client_secret_env = "1X"'),
        SECRET_ENV_MSG,
    ),
    (
        "secret-env-newline",
        _replace(FULL, CLIENT_ID, CLIENT_ID + '\ngoogle_client_secret_env = "X\\n"'),
        SECRET_ENV_MSG,
    ),
    ("secret-env-not-string", _replace(FULL, CLIENT_ID, CLIENT_ID + "\ngoogle_client_secret_env = 1"), SECRET_ENV_MSG),
    (
        "domains-not-list",
        _replace(FULL, DOMAINS, 'allowed_domains = "example.com"'),
        "auth.allowed_domains must be a list of strings",
    ),
    (
        "domains-empty-entry",
        _replace(FULL, DOMAINS, 'allowed_domains = [""]'),
        "auth.allowed_domains must be a list of strings",
    ),
    (
        "domains-number-entry",
        _replace(FULL, DOMAINS, "allowed_domains = [1]"),
        "auth.allowed_domains must be a list of strings",
    ),
    (
        "emails-not-list",
        _replace(FULL, EMAILS, 'allowed_emails = "a@b.c"'),
        "auth.allowed_emails must be a list of strings",
    ),
    (
        "both-lists-empty",
        _replace(_replace(FULL, DOMAINS, "allowed_domains = []"), EMAILS, "allowed_emails = []"),
        "auth.allowed_domains or auth.allowed_emails must name who may sign in",
    ),
    (
        "both-lists-missing",
        _replace(_replace(FULL, DOMAINS, ""), EMAILS, ""),
        "auth.allowed_domains or auth.allowed_emails must name who may sign in",
    ),
    (
        "email-without-at",
        _replace(FULL, EMAILS, 'allowed_emails = ["guest"]'),
        "auth.allowed_emails entry 'guest' is not an e-mail address",
    ),
    (
        "email-two-ats",
        _replace(FULL, EMAILS, 'allowed_emails = ["a@b@c"]'),
        "auth.allowed_emails entry 'a@b@c' is not an e-mail address",
    ),
    (
        "redirect-not-list",
        _replace(FULL, EMAILS, EMAILS + '\nredirect_uri_allowlist = "https://a.example/cb"'),
        REDIRECT_MSG,
    ),
    (
        "redirect-http",
        _replace(FULL, EMAILS, EMAILS + '\nredirect_uri_allowlist = ["http://a.example/cb"]'),
        REDIRECT_MSG,
    ),
    ("redirect-unparseable", _replace(FULL, EMAILS, EMAILS + '\nredirect_uri_allowlist = ["https://["]'), REDIRECT_MSG),
]


class TestLoadConfigErrors:
    @pytest.mark.parametrize(("text", "message"), [pytest.param(t, m, id=i) for i, t, m in BAD_FILES])
    def test_message(self, tmp_path: Path, text: str, message: str) -> None:
        path = write(tmp_path, text)
        with pytest.raises(ConfigError) as exc:
            load_config(path, {})
        assert str(exc.value).startswith(f"{path}: {message}")
        if not message.endswith(": "):
            assert str(exc.value) == f"{path}: {message}"

    def test_missing_file(self, tmp_path: Path) -> None:
        path = tmp_path / "absent.toml"
        with pytest.raises(ConfigError) as exc:
            load_config(path, {})
        assert str(exc.value) == (
            f"{path}: No configuration file here. Pass --config, or set HTML_ARTIFACT_DEPLOY_CONFIG."
        )

    def test_undecodable_bytes_are_not_valid_toml(self, tmp_path: Path) -> None:
        path = tmp_path / "bad.toml"
        path.write_bytes(b"\xff\xfe")
        with pytest.raises(ConfigError, match="not valid TOML"):
            load_config(path, {})

    def test_checks_run_in_order(self, tmp_path: Path) -> None:
        text = _replace(_replace(MINIMAL, ROOT, 'root = "rel"'), STATE_DIR, 'dir = "rel"')
        with pytest.raises(ConfigError, match="pages.root"):
            load(tmp_path, text)


class TestResolveConfigPath:
    def test_cli_beats_env_beats_default(self) -> None:
        env = {CONFIG_ENV: "/from/env.toml"}
        assert resolve_config_path("/from/cli.toml", env) == Path("/from/cli.toml")
        assert resolve_config_path(None, env) == Path("/from/env.toml")
        assert resolve_config_path(None, {}) == DEFAULT_CONFIG_PATH
        assert resolve_config_path("", {CONFIG_ENV: ""}) == DEFAULT_CONFIG_PATH


class TestRequireHttp:
    def test_returns_both_sections(self, tmp_path: Path) -> None:
        config = load(tmp_path, FULL)
        http, auth = require_http(config)
        assert http is config.http
        assert auth is config.auth

    @pytest.mark.parametrize("text", [MINIMAL, MINIMAL + AUTH.split("[auth]")[0]])
    def test_refuses_a_file_without_http_and_auth(self, tmp_path: Path, text: str) -> None:
        config = load(tmp_path, text)
        with pytest.raises(ConfigError) as exc:
            require_http(config)
        assert str(exc.value) == f"{config.path}: serve-http needs the [http] and [auth] sections"

    def test_refuses_auth_without_http(self, tmp_path: Path) -> None:
        config = load(tmp_path, MINIMAL + "[auth" + AUTH.split("[auth")[1])
        assert config.http is None
        with pytest.raises(ConfigError):
            require_http(config)


class TestIsAllowedEmail:
    AUTH = AuthConfig("id.apps.googleusercontent.com", ("example.com",), ("guest@other.org",))

    def test_listed_address_in_any_case(self) -> None:
        assert is_allowed_email(self.AUTH, "Guest@Other.ORG", None)

    def test_hosted_domain_in_allowed_domains(self) -> None:
        assert is_allowed_email(self.AUTH, "anyone@elsewhere.net", "Example.com")

    def test_no_hosted_domain_means_no(self) -> None:
        assert not is_allowed_email(self.AUTH, "someone@other.org", None)

    def test_other_hosted_domain_means_no(self) -> None:
        assert not is_allowed_email(self.AUTH, "someone@example.com", "other.org")

    def test_own_domain_alone_does_not_count(self) -> None:
        assert not is_allowed_email(self.AUTH, "someone@example.com", None)


class TestGoogleClientSecret:
    AUTH = AuthConfig("id.apps.googleusercontent.com", ("example.com",), (), "MY_SECRET")

    def test_returns_the_value(self) -> None:
        assert google_client_secret(self.AUTH, {"MY_SECRET": "s3cret"}) == "s3cret"

    @pytest.mark.parametrize("environ", [{}, {"MY_SECRET": ""}])
    def test_unset_or_empty_is_an_error(self, environ: dict[str, str]) -> None:
        with pytest.raises(ConfigError) as exc:
            google_client_secret(self.AUTH, environ)
        assert str(exc.value) == (
            "the environment variable MY_SECRET is empty; it must hold the Google OAuth client secret"
        )
