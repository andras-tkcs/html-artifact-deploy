"""The TOML configuration file: `Config`, its sections, and `load_config`.

`load_config` checks every setting and raises `ConfigError` with a message that says what to fix.
Unknown keys are errors, not ignored, so a typo in a security setting is never silently dropped.
It never reads the Google client secret and never touches `pages.root` or `state.dir` on disk.
"""

from __future__ import annotations

import ipaddress
import re
import tomllib
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from urllib.parse import SplitResult, urlsplit

CONFIG_ENV = "HTML_ARTIFACT_DEPLOY_CONFIG"
DEFAULT_CONFIG_PATH = Path("/etc/html-artifact-deploy/config.toml")
DEFAULT_MAX_BYTES = 16_000_000  # the size limit of a claude.ai artifact
MAX_MAX_BYTES = 50_000_000
DEFAULT_EXPIRY_DAYS = 90  # about three months
MAX_EXPIRY_DAYS = 365  # twelve months; also the ceiling an administrator can set
DEFAULT_SECRET_ENV = "HTML_ARTIFACT_DEPLOY_GOOGLE_CLIENT_SECRET"  # nosec B105  # a variable name, not a secret
DEFAULT_REDIRECT_URI_ALLOWLIST = (
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
)

_SECTIONS: dict[str, tuple[str, ...]] = {
    "pages": ("root", "public_base_url", "max_bytes", "default_expiry_days", "max_expiry_days"),
    "state": ("dir",),
    "stdio": ("user",),
    "http": ("public_url", "listen_host", "listen_port"),
    "auth": (
        "google_client_id",
        "google_client_secret_env",
        "allowed_domains",
        "allowed_emails",
        "redirect_uri_allowlist",
    ),
    "fetch": ("allowed_hosts", "timeout_seconds"),
}
_SECRET_ENV_RE = re.compile(r"[A-Z_][A-Z0-9_]*")
_LOCAL_HOSTS = ("127.0.0.1", "localhost")
# A dotted DNS name whose last label starts with a letter: wildcards, ports, schemes and IP literals never match.
HOST_RE = re.compile(r"(?=.{1,253}\Z)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]([a-z0-9-]{0,61}[a-z0-9])?")


class ConfigError(ValueError):
    """The configuration file is missing or wrong; the message says what to fix."""


@dataclass(frozen=True)
class PagesConfig:
    root: Path
    public_base_url: str  # no trailing slash
    max_bytes: int = DEFAULT_MAX_BYTES
    default_expiry_days: int = DEFAULT_EXPIRY_DAYS
    max_expiry_days: int = MAX_EXPIRY_DAYS


@dataclass(frozen=True)
class HttpConfig:
    public_url: str  # no trailing slash
    listen_host: str = "127.0.0.1"
    listen_port: int = 8765


@dataclass(frozen=True)
class AuthConfig:
    google_client_id: str
    allowed_domains: tuple[str, ...]
    allowed_emails: tuple[str, ...]
    google_client_secret_env: str = DEFAULT_SECRET_ENV  # the NAME of the variable holding the secret
    redirect_uri_allowlist: tuple[str, ...] = DEFAULT_REDIRECT_URI_ALLOWLIST


@dataclass(frozen=True)
class FetchConfig:
    allowed_hosts: tuple[str, ...]  # lowercased host names
    timeout_seconds: int = 20


@dataclass(frozen=True)
class Config:
    path: Path
    pages: PagesConfig
    state_dir: Path
    stdio_user: str = "local"
    http: HttpConfig | None = None
    auth: AuthConfig | None = None
    fetch: FetchConfig | None = None


def resolve_config_path(cli_value: str | None, environ: Mapping[str, str]) -> Path:
    """The `--config` value, else `HTML_ARTIFACT_DEPLOY_CONFIG`, else `DEFAULT_CONFIG_PATH`."""
    if cli_value:
        return Path(cli_value)
    from_env = environ.get(CONFIG_ENV)
    if from_env:
        return Path(from_env)
    return DEFAULT_CONFIG_PATH


def _split(value: object) -> SplitResult | None:
    if not isinstance(value, str):
        return None
    try:
        return urlsplit(value)
    except ValueError:
        return None


def _https_url(value: object) -> str | None:
    """Return `value` without a trailing `/` if it is a plain https URL (http for local hosts)."""
    parts = _split(value)
    if (
        not isinstance(value, str)
        or parts is None
        or not parts.hostname
        or parts.query
        or parts.fragment
        or "@" in parts.netloc
    ):
        return None
    if parts.scheme != "https" and not (parts.scheme == "http" and parts.hostname in _LOCAL_HOSTS):
        return None
    return value.rstrip("/")


def _is_int(value: object, low: int, high: int) -> bool:
    return isinstance(value, int) and not isinstance(value, bool) and low <= value <= high


def _is_ip_address(value: str) -> bool:
    try:
        ipaddress.ip_address(value)
    except ValueError:
        return False
    return True


def _string_list(value: object) -> bool:
    return isinstance(value, list) and all(isinstance(item, str) and item for item in value)


def load_config(path: Path, environ: Mapping[str, str]) -> Config:
    """Read and check the configuration file at `path`; `ConfigError` on any problem.

    `environ` is accepted for symmetry with `google_client_secret`; no setting is read from it.
    """

    def fail(message: str) -> ConfigError:
        return ConfigError(f"{path}: {message}")

    try:
        with path.open("rb") as handle:
            data: dict[str, Any] = tomllib.load(handle)
    except FileNotFoundError:
        raise fail("No configuration file here. Pass --config, or set HTML_ARTIFACT_DEPLOY_CONFIG.") from None
    except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
        raise fail(f"not valid TOML: {exc}") from None

    for name in data:
        if name not in _SECTIONS:
            raise fail(f"unknown section [{name}]")
    for name, section in data.items():
        if not isinstance(section, dict):
            raise fail(f"[{name}] must be a table")
        for key in section:
            if key not in _SECTIONS[name]:
                raise fail(f"unknown key {name}.{key}")

    if "pages" not in data:
        raise fail("the [pages] section is required")
    pages: dict[str, Any] = data["pages"]

    root = pages.get("root")
    if not isinstance(root, str) or not root or not Path(root).is_absolute():
        raise fail(
            "pages.root must be the absolute path of the folder the web server publishes, for example /srv/pages"
        )
    public_base_url = _https_url(pages.get("public_base_url"))
    if public_base_url is None:
        raise fail(
            "pages.public_base_url must be the https:// address at which the web server publishes pages.root, "
            "for example https://pages.example.com"
        )
    max_bytes = pages.get("max_bytes", DEFAULT_MAX_BYTES)
    if not _is_int(max_bytes, 1, MAX_MAX_BYTES):
        raise fail(f"pages.max_bytes must be a whole number of bytes from 1 to {MAX_MAX_BYTES}")
    max_expiry_days = pages.get("max_expiry_days", MAX_EXPIRY_DAYS)
    if not _is_int(max_expiry_days, 1, MAX_EXPIRY_DAYS):
        raise fail(f"pages.max_expiry_days must be a whole number of days from 1 to {MAX_EXPIRY_DAYS}")
    default_expiry_days = pages.get("default_expiry_days", DEFAULT_EXPIRY_DAYS)
    if not _is_int(default_expiry_days, 1, max_expiry_days):
        raise fail(
            f"pages.default_expiry_days must be a whole number of days from 1 to pages.max_expiry_days ({max_expiry_days})"
        )

    state_dir = data.get("state", {}).get("dir")
    if not isinstance(state_dir, str) or not state_dir or not Path(state_dir).is_absolute():
        raise fail(
            "state.dir must be the absolute path of a folder this server keeps its database in, "
            "for example /var/lib/html-artifact-deploy"
        )

    stdio_user = data.get("stdio", {}).get("user", "local")
    if not isinstance(stdio_user, str) or not 1 <= len(stdio_user) <= 200:
        raise fail("stdio.user must be a non-empty name, at most 200 characters")

    http: HttpConfig | None = None
    if "http" in data:
        raw = data["http"]
        public_url = _https_url(raw.get("public_url"))
        if public_url is None:
            raise fail(
                "http.public_url must be the https:// address of this server, for example https://publish.example.com"
            )
        listen_host = raw.get("listen_host", "127.0.0.1")
        if not isinstance(listen_host, str) or not listen_host:
            raise fail("http.listen_host must be an address to listen on, for example 127.0.0.1")
        listen_port = raw.get("listen_port", 8765)
        if not _is_int(listen_port, 1, 65535):
            raise fail("http.listen_port must be a whole number from 1 to 65535")
        if urlsplit(public_base_url).hostname == urlsplit(public_url).hostname:
            raise fail(
                "pages.public_base_url must be on a different host name from http.public_url: "
                "a published page runs its own scripts, and must not share an origin with this server"
            )
        http = HttpConfig(public_url=public_url, listen_host=listen_host, listen_port=listen_port)

    auth: AuthConfig | None = None
    if "auth" in data:
        raw = data["auth"]
        client_id = raw.get("google_client_id")
        if not isinstance(client_id, str) or not client_id.endswith(".apps.googleusercontent.com"):
            raise fail(
                "auth.google_client_id must be the Client ID of a Google OAuth client, "
                "ending in .apps.googleusercontent.com"
            )
        secret_env = raw.get("google_client_secret_env", DEFAULT_SECRET_ENV)
        if not isinstance(secret_env, str) or not _SECRET_ENV_RE.fullmatch(secret_env):
            raise fail(
                "auth.google_client_secret_env must be the name of an environment variable, "
                "for example HTML_ARTIFACT_DEPLOY_GOOGLE_CLIENT_SECRET"
            )
        lists: dict[str, tuple[str, ...]] = {}
        for key in ("allowed_domains", "allowed_emails"):
            value = raw.get(key, [])
            if not _string_list(value):
                raise fail(f"auth.{key} must be a list of strings")
            lists[key] = tuple(item.lower() for item in value)
        if not lists["allowed_domains"] and not lists["allowed_emails"]:
            raise fail("auth.allowed_domains or auth.allowed_emails must name who may sign in")
        for email in lists["allowed_emails"]:
            if email.count("@") != 1:
                raise fail(f"auth.allowed_emails entry {email!r} is not an e-mail address")
        redirects = raw.get("redirect_uri_allowlist", list(DEFAULT_REDIRECT_URI_ALLOWLIST))
        if not _string_list(redirects) or any(getattr(_split(uri), "scheme", None) != "https" for uri in redirects):
            raise fail("auth.redirect_uri_allowlist must be a list of https:// redirect URIs")
        auth = AuthConfig(
            google_client_id=client_id,
            allowed_domains=lists["allowed_domains"],
            allowed_emails=lists["allowed_emails"],
            google_client_secret_env=secret_env,
            redirect_uri_allowlist=tuple(redirects),
        )

    fetch: FetchConfig | None = None
    if "fetch" in data:
        raw = data["fetch"]
        hosts = raw.get("allowed_hosts")
        if not isinstance(hosts, list) or not hosts or not all(isinstance(item, str) for item in hosts):
            raise fail(
                "fetch.allowed_hosts must list the host names pages may be fetched from, "
                'for example ["raw.githubusercontent.com"]'
            )
        own_hosts = {urlsplit(public_base_url).hostname}
        if http is not None:
            own_hosts.add(urlsplit(http.public_url).hostname)
        for item in hosts:
            host = item.lower()
            if _is_ip_address(host) or not HOST_RE.fullmatch(host):
                raise fail(
                    f"fetch.allowed_hosts entry {item!r} must be a host name such as raw.githubusercontent.com, "
                    "not an address, URL or pattern"
                )
            if host in own_hosts:
                raise fail(f"fetch.allowed_hosts must not name this server's own host names ({host})")
        timeout = raw.get("timeout_seconds", 20)
        if not _is_int(timeout, 1, 120):
            raise fail("fetch.timeout_seconds must be a whole number of seconds from 1 to 120")
        fetch = FetchConfig(allowed_hosts=tuple(item.lower() for item in hosts), timeout_seconds=timeout)

    return Config(
        path=path,
        pages=PagesConfig(
            root=Path(root),
            public_base_url=public_base_url,
            max_bytes=max_bytes,
            default_expiry_days=default_expiry_days,
            max_expiry_days=max_expiry_days,
        ),
        state_dir=Path(state_dir),
        stdio_user=stdio_user,
        http=http,
        auth=auth,
        fetch=fetch,
    )


def require_http(config: Config) -> tuple[HttpConfig, AuthConfig]:
    """The `[http]` and `[auth]` sections `serve-http` needs; `ConfigError` if either is absent."""
    if config.http is None or config.auth is None:
        raise ConfigError(f"{config.path}: serve-http needs the [http] and [auth] sections")
    return config.http, config.auth


def is_allowed_email(auth: AuthConfig, email: str, hosted_domain: str | None) -> bool:
    """Whether `email` may sign in: listed, or its Google `hd` claim is an allowed domain.

    Google sets `hd` only for accounts managed by that Workspace, so `hd` alone decides; the
    e-mail's own domain is not compared.
    """
    if email.lower() in auth.allowed_emails:
        return True
    return hosted_domain is not None and hosted_domain.lower() in auth.allowed_domains


def google_client_secret(auth: AuthConfig, environ: Mapping[str, str]) -> str:
    """The Google OAuth client secret from the environment; `ConfigError` if unset or empty."""
    name = auth.google_client_secret_env
    value = environ.get(name, "")
    if not value:
        raise ConfigError(f"the environment variable {name} is empty; it must hold the Google OAuth client secret")
    return value
