# Plan: publish single-file HTML pages from Claude, on a server the organization runs

## Goal

Claude (claude.ai, Claude Desktop, Claude Code) makes single-file HTML artifacts. This project
turns the template skeleton into a complete MCP server that publishes such a page onto a web
server the organization runs and answers with the page's link. Anyone with the link can open the
page. The person who published a page can replace it at the same link, list their pages and take
one down. People sign in with their Google Workspace account. claude.ai and Claude Desktop connect
through OAuth; Claude Code connects through OAuth or a personal API token. Large pages go through
a one-time upload URL instead of a tool argument, or, when the page is already online on a host
the administrator allows, the server downloads it from its https address.

The feature began as a PrivacyFence connector plan (PrivacyFence repository, branch
`plan/web-pages`), and was moved out because publishing is a service of its own rather than a
gate in front of an existing system. That plan's page-level decisions carry over: random-id links,
a separate host name with a sandbox header, per-person ownership, no directory listing. What
changes is how pages reach the web server: this server runs *on* the web server and writes into
the folder it serves, behind a storage interface so a remote backend can be added later.

There is no tracking issue.

## Current state

The repository is the MCP server template, renamed:

- `src/html_artifact_deploy/server.py:16-45`: `build_server()` builds an `MCPServer` with two
  placeholder tools, `echo` and `server_info`.
- `src/html_artifact_deploy/__main__.py:10-17`: `main()` parses only `--version` and runs stdio.
- Tests: `tests/unit/test_server.py` (tool list `{"echo", "server_info"}` at line 22),
  `tests/unit/test_main.py`, `tests/integration/test_stdio_contract.py` (calls `echo`),
  `tests/packaged/test_installed_server.py` (calls `server_info` with no arguments or config),
  `tests/unit/test_no_project_history.py` (ADR 0008 guard).
- `tests/conftest.py:15-16`: `_reset()` is empty; there is no module-level state.
- `scripts/live_check.py:52`: `CHECKS` is empty.
- `scripts/check_coverage_floor.py:31`: `OVERALL_FLOOR = 100.0`. **Every phase must leave the
  whole suite at 100% line and branch coverage**, or the `test` job fails.
- `pyproject.toml`: the only runtime dependency is `mcp>=2.2,<3.0.0`. Installed: `mcp 2.2.0`, which
  brings `starlette 1.7`, `uvicorn 0.54`, `httpx2 2.13` (import name `httpx2`, with
  `httpx2.ASGITransport`), `pyjwt`, `python-multipart`. Route tests use
  `httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=<http.public_url>)` inside
  `async with app.router.lifespan_context(app):` (the MCP session manager starts in the lifespan);
  nothing else.
- mypy strict ratchet: one `[[tool.mypy.overrides]]` block for `html_artifact_deploy.server`.
  `scripts/mypy_strict_modules.py` refuses wildcards and refuses a listed module whose file does
  not exist, so modules are promoted by name, in a phase that runs after the module exists (D1).
- `scripts/check_coverage_floor.py:33`: `MODULE_FLOORS` lists `server.py` at 100; its docstring asks
  for security-critical modules to be listed too.
- `bandit` flags string constants whose names contain `TOKEN`, `SECRET` or `PASSWORD` (B105) and
  `token_type="Bearer"` keyword arguments (B106); each such site gets
  `# nosec B105  # <what the string is>` or `# nosec B106  # <reason>`.
- ADRs 0001–0010 exist. ADR 0010 makes Linux the only supported OS (`tests.yml` and `build.yml` run on
  Linux only), so POSIX file modes may be asserted without platform skips. The next free number is
  **0011**.

What the MCP SDK 2.2 provides and the design relies on (read these files in
`.venv/lib/python3.11/site-packages/mcp/`):

- `server/mcpserver/server.py`: `MCPServer(name, auth_server_provider=..., auth=AuthSettings(...))`,
  `@server.tool(annotations=ToolAnnotations(...))`, `server.custom_route(path, methods)`,
  `server.streamable_http_app(streamable_http_path=, json_response=, stateless_http=,
  max_request_body_size=, transport_security=, host=)`.
- `server/mcpserver/exceptions.py`: `ToolError`; raising it returns a tool error with its message.
- `server/auth/provider.py`: the `OAuthAuthorizationServerProvider` protocol, `AuthorizationParams`,
  `AuthorizationCode`, `RefreshToken`, `AccessToken` (has `subject`), `RegistrationError`,
  `AuthorizeError`, `TokenError`, `construct_redirect_uri`.
- `server/auth/settings.py`: `AuthSettings`, `ClientRegistrationOptions`, `RevocationOptions`.
- `server/auth/middleware/auth_context.py`: `get_access_token()`.
- `server/transport_security.py`: `TransportSecuritySettings(enable_dns_rebinding_protection,
  allowed_hosts, allowed_origins)`, `DEFAULT_MAX_REQUEST_BODY_SIZE = 4 MiB`.
- `client/streamable_http.py`: `streamable_http_client(url, http_client=httpx2.AsyncClient(...))`;
  `mcp.Client` accepts a URL string or a transport.

## Design

### D1. Shape and module map

One Python package, one process per deployment, two transports built from one factory.

| Module (`src/html_artifact_deploy/`) | Holds |
|---|---|
| `config.py` | The TOML configuration file: `Config` and its sections, `load_config`, `ConfigError` (D2) |
| `pages.py` | Page ids, slugs, titles, links (D3) |
| `storage.py` | `PageStore` protocol, `LocalFolderStore`, `StorageError` (D4) |
| `state.py` | `StateDB`: the one SQLite file and its schema (D5) |
| `page_index.py` | `PageIndex`, `PageRecord`: who published which page (D6) |
| `service.py` | `PageService`: publish, list, unpublish; `PageError` (D7) |
| `server.py` | `AppContext`, `build_server()`: the MCP tools (D8) |
| `google_oidc_client.py` | `GoogleOidcClient`, `GoogleIdentity`, `GoogleOidcClientError` (D9) |
| `token_store.py` | `TokenStore`: OAuth clients, pending sign-ins, codes, tokens, API tokens (D10) |
| `oauth_provider.py` | `GoogleOAuthProvider`: the OAuth 2.1 authorization server (D10) |
| `uploads.py` | `UploadStore`, `UploadError`: one-time upload URLs (D12) |
| `fetch_client.py` | `FetchClient`, `FetchClientError`: download a page from an allowlisted https host (D16) |
| `http_app.py` | `build_http_app()`: Streamable HTTP, OAuth routes, upload route, health check (D11) |
| `__main__.py` | The command line: stdio (default), `serve-http`, `check-config`, `token …` (D13) |

No module holds module-level mutable state; every store is an instance built by `__main__` or
`build_http_app`, so `tests/conftest.py` stays unchanged.

Every new module must pass `mypy --strict` from its first commit (each phase's acceptance runs
it on its own files). Promotion into the blocking ratchet happens by name in sequential phases,
turning the existing block's `module = "html_artifact_deploy.server"` into a list:
p3 adds `config`, `pages`, `storage`, `state`, `page_index`, `service`; p6 adds
`google_oidc_client`, `token_store`, `oauth_provider`, `http_app`, `__main__`; p7 adds `uploads`;
p8 adds `fetch_client`.
Coverage floors at 100 (`MODULE_FLOORS` in `scripts/check_coverage_floor.py`, with a one-line
comment each) are added in the same phases for `config.py`, `storage.py` (p3), `token_store.py`,
`oauth_provider.py`, `http_app.py` (p6), `uploads.py` (p7) and `fetch_client.py` (p8).

`CHANGELOG.md` is written only by p10; no other phase edits it.

Blocking work (SQLite, file I/O, the Google HTTP call) is synchronous inside its module and called
from `async def` code through `asyncio.to_thread(...)` (coding guidelines, "Async and
concurrency"). Timestamps are `int(clock())` seconds in the database and ISO 8601 UTC strings
(`datetime.fromtimestamp(t, UTC).isoformat(timespec="seconds")`, e.g.
`2026-09-30T17:00:00+00:00`) in tool results. Every class that reads the time takes
`clock: Callable[[], float] = time.time`.

### D2. Configuration: `config.py`

A TOML file read with `tomllib`. Path: the `--config PATH` option, else the environment variable
`HTML_ARTIFACT_DEPLOY_CONFIG`, else `/etc/html-artifact-deploy/config.toml`
(`DEFAULT_CONFIG_PATH`). Constant `CONFIG_ENV = "HTML_ARTIFACT_DEPLOY_CONFIG"`.

```toml
[pages]
root = "/srv/pages"                            # required
public_base_url = "https://pages.example.com"  # required
max_bytes = 16000000                           # optional

[state]
dir = "/var/lib/html-artifact-deploy"          # required

[stdio]
user = "local"                                 # optional

[http]                                         # required for serve-http
public_url = "https://publish.example.com"
listen_host = "127.0.0.1"                      # optional
listen_port = 8765                             # optional

[auth]                                         # required for serve-http
google_client_id = "1234-abc.apps.googleusercontent.com"
google_client_secret_env = "HTML_ARTIFACT_DEPLOY_GOOGLE_CLIENT_SECRET"   # optional
allowed_domains = ["example.com"]
allowed_emails = []
redirect_uri_allowlist = ["https://claude.ai/api/mcp/auth_callback", "https://claude.com/api/mcp/auth_callback"]  # optional

[fetch]                                        # optional; turns on publish_page_from_url (D16)
allowed_hosts = ["raw.githubusercontent.com"]
timeout_seconds = 20                           # optional
```

The `[fetch]` section, `FetchConfig` and its rows in the table below are added by p8; until then
`[fetch]` is an unknown section like any other.

Dataclasses, all `@dataclass(frozen=True)`:

```python
class ConfigError(ValueError): ...

@dataclass(frozen=True)
class PagesConfig:
    root: Path
    public_base_url: str        # no trailing slash
    max_bytes: int = DEFAULT_MAX_BYTES

@dataclass(frozen=True)
class HttpConfig:
    public_url: str             # no trailing slash
    listen_host: str = "127.0.0.1"
    listen_port: int = 8765

@dataclass(frozen=True)
class AuthConfig:
    google_client_id: str
    allowed_domains: tuple[str, ...]
    allowed_emails: tuple[str, ...]
    google_client_secret_env: str = DEFAULT_SECRET_ENV   # the NAME of the variable holding the secret
    redirect_uri_allowlist: tuple[str, ...] = DEFAULT_REDIRECT_URI_ALLOWLIST

@dataclass(frozen=True)
class Config:
    path: Path
    pages: PagesConfig
    state_dir: Path
    stdio_user: str = "local"
    http: HttpConfig | None = None
    auth: AuthConfig | None = None
    fetch: FetchConfig | None = None                     # added by p8

@dataclass(frozen=True)
class FetchConfig:                                       # added by p8
    allowed_hosts: tuple[str, ...]                       # lowercased host names
    timeout_seconds: int = 20

DEFAULT_MAX_BYTES = 16_000_000      # the size limit of a claude.ai artifact
MAX_MAX_BYTES = 50_000_000
DEFAULT_SECRET_ENV = "HTML_ARTIFACT_DEPLOY_GOOGLE_CLIENT_SECRET"  # nosec B105  # a variable name, not a secret
DEFAULT_REDIRECT_URI_ALLOWLIST = ("https://claude.ai/api/mcp/auth_callback", "https://claude.com/api/mcp/auth_callback")

def resolve_config_path(cli_value: str | None, environ: Mapping[str, str]) -> Path: ...
def load_config(path: Path, environ: Mapping[str, str]) -> Config: ...
def require_http(config: Config) -> tuple[HttpConfig, AuthConfig]: ...
def is_allowed_email(auth: AuthConfig, email: str, hosted_domain: str | None) -> bool: ...
def google_client_secret(auth: AuthConfig, environ: Mapping[str, str]) -> str: ...
```

`load_config` raises `ConfigError(f"{path}: {message}")` for every problem, checked in this
order. `<https URL>` below means: `urllib.parse.urlsplit` gives scheme `https` (or `http` when the
host is `127.0.0.1` or `localhost`, for tests), a host, no query, no fragment, no user info; a
trailing `/` is stripped. Allowed-email and domain values are lowercased on load.

| Check | Message |
|---|---|
| file missing | `No configuration file here. Pass --config, or set HTML_ARTIFACT_DEPLOY_CONFIG.` |
| not valid TOML (`tomllib.TOMLDecodeError`) | `not valid TOML: {exc}` |
| a top-level key other than `pages`, `state`, `stdio`, `http`, `auth` (and, from p8, `fetch`) | `unknown section [{name}]` |
| a key inside a section not listed above | `unknown key {section}.{key}` |
| `pages` missing | `the [pages] section is required` |
| `pages.root` not a non-empty string naming an absolute path | `pages.root must be the absolute path of the folder the web server publishes, for example /srv/pages` |
| `pages.public_base_url` not `<https URL>` | `pages.public_base_url must be the https:// address at which the web server publishes pages.root, for example https://pages.example.com` |
| `pages.max_bytes` present and not an `int` (not `bool`) in 1…50 000 000 | `pages.max_bytes must be a whole number of bytes from 1 to 50000000` |
| `state.dir` missing or not an absolute path | `state.dir must be the absolute path of a folder this server keeps its database in, for example /var/lib/html-artifact-deploy` |
| `stdio.user` present and not a non-empty string of at most 200 characters | `stdio.user must be a non-empty name, at most 200 characters` |
| `http.public_url` missing or not `<https URL>` | `http.public_url must be the https:// address of this server, for example https://publish.example.com` |
| `http.listen_host` present and not a non-empty string | `http.listen_host must be an address to listen on, for example 127.0.0.1` |
| `http.listen_port` present and not an `int` in 1…65535 | `http.listen_port must be a whole number from 1 to 65535` |
| `pages.public_base_url` and `http.public_url` share a host name (compare `urlsplit(...).hostname`) | `pages.public_base_url must be on a different host name from http.public_url: a published page runs its own scripts, and must not share an origin with this server` |
| `auth.google_client_id` missing or not ending in `.apps.googleusercontent.com` | `auth.google_client_id must be the Client ID of a Google OAuth client, ending in .apps.googleusercontent.com` |
| `auth.google_client_secret_env` present and not matching `^[A-Z_][A-Z0-9_]*$` | `auth.google_client_secret_env must be the name of an environment variable, for example HTML_ARTIFACT_DEPLOY_GOOGLE_CLIENT_SECRET` |
| `auth.allowed_domains` / `auth.allowed_emails` not a list of non-empty strings | `auth.{key} must be a list of strings` |
| both lists empty | `auth.allowed_domains or auth.allowed_emails must name who may sign in` |
| an `allowed_emails` entry without exactly one `@` | `auth.allowed_emails entry {value!r} is not an e-mail address` |
| `auth.redirect_uri_allowlist` present and not a list of `https://` URLs (plain `urlsplit` scheme check) | `auth.redirect_uri_allowlist must be a list of https:// redirect URIs` |
| (p8) `fetch.allowed_hosts` missing, empty, or not a list of strings | `fetch.allowed_hosts must list the host names pages may be fetched from, for example ["raw.githubusercontent.com"]` |
| (p8) an entry, lowercased, that `ipaddress.ip_address` accepts, or that does not match `HOST_RE` | `fetch.allowed_hosts entry {value!r} must be a host name such as raw.githubusercontent.com, not an address, URL or pattern` |
| (p8) an entry equal to the host name of `http.public_url` or of `pages.public_base_url` | `fetch.allowed_hosts must not name this server's own host names ({value})` |
| (p8) `fetch.timeout_seconds` present and not an `int` in 1…120 | `fetch.timeout_seconds must be a whole number of seconds from 1 to 120` |

The secret itself is not read by `load_config`, so stdio, `check-config` without the
variable, and the `token` commands work without it. `google_client_secret(auth, environ)` returns
`environ[auth.google_client_secret_env]`, or raises
`ConfigError(f"the environment variable {name} is empty; it must hold the Google OAuth client secret")`
if it is unset or empty. `serve-http` calls it before building the app, and `check-config` calls
it when `[auth]` is present.

Unknown keys are errors, not ignored: a typo in a security setting must not be silently
dropped. `load_config` never touches `pages.root` or `state.dir` on disk; `LocalFolderStore` and
`StateDB` check those when built.

`HOST_RE = re.compile(r"(?=.{1,253}\Z)([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]([a-z0-9-]{0,61}[a-z0-9])?")`,
used only as `HOST_RE.fullmatch(value)` (added by p8): a dotted DNS name whose last label starts with a letter. Wildcards, ports, schemes
and IP literals are refused, so every allowed host is a name an administrator wrote out.

`require_http(config)` returns `(config.http, config.auth)`, or raises
`ConfigError(f"{config.path}: serve-http needs the [http] and [auth] sections")` if either is
`None`.

`is_allowed_email(auth, email, hosted_domain)`: `True` if `email.lower() in auth.allowed_emails`,
or if `hosted_domain` is not `None` and `hosted_domain.lower() in auth.allowed_domains`. Google
sets `hd` only for accounts managed by that Workspace, and to the primary domain even for users on
a secondary domain, so `hd` alone decides; the e-mail's own domain is not compared. For API tokens
(D13), which have no Google `hd` claim, the caller passes `hosted_domain = email.rpartition("@")[2]`.

### D3. Ids, slugs, links: `pages.py`

```python
PAGE_ID_RE = re.compile(r"^[a-z2-7]{24}$")
SLUG_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,59}$")
MAX_TITLE = 200

def new_page_id() -> str          # base64.b32encode(secrets.token_bytes(15)).decode("ascii").lower(): 24 chars, 120 bits
def slugify(title: str) -> str
def is_page_id(value: str) -> bool
def page_url(public_base_url: str, page_id: str, slug: str) -> str   # f"{public_base_url}/{page_id}/{slug}.html"
def clean_title(title: str) -> str   # strip; raises ValueError("title must be 1 to 200 characters.") if the result is 0 or >200 chars
```

`slugify`: `unicodedata.normalize("NFKD", title).encode("ascii", "ignore").decode("ascii").lower()`;
replace each run of characters outside `[a-z0-9]` with `-`; strip `-` from both ends; cut to 60
characters; strip `-` again; `"page"` if empty. `slugify("Q3 report: Ünits & Co.") ==
"q3-report-units-co"`. `page_url` raises `ValueError` unless `is_page_id(page_id)` and
`SLUG_RE.match(slug)`.

### D4. Storage: `storage.py`

```python
class StorageError(Exception): ...

class PageStore(Protocol):
    def check(self) -> None: ...                                  # raises StorageError if unusable
    def write_page(self, page_id: str, slug: str, data: bytes) -> None: ...
    def delete_page(self, page_id: str) -> bool: ...              # False if there was nothing to delete

class LocalFolderStore:
    def __init__(self, root: Path) -> None: ...
```

The protocol is the seam for a later remote backend (for example SFTP); nothing outside
`storage.py`, `__main__.py` and `http_app.py` names `LocalFolderStore`.

`LocalFolderStore`:
- `check()`: `root.is_dir()` and `os.access(root, os.W_OK)`, else
  `StorageError(f"The pages folder {root} does not exist or this server cannot write to it.")`.
- `write_page`: `ValueError` unless `pages.is_page_id(page_id)` and `pages.SLUG_RE` matches
  `slug`. `folder = root / page_id`; `StorageError("The pages folder contains a link where a page folder should be.")`
  if `folder.is_symlink()`; `folder.mkdir(mode=0o755, exist_ok=True)`; `os.chmod(folder, 0o755)`;
  `fd, tmp = tempfile.mkstemp(dir=folder, prefix=f".{slug}.", suffix=".tmp")`; write all bytes;
  `os.fsync`; close; `os.chmod(tmp, 0o644)`; `os.replace(tmp, folder / f"{slug}.html")`. On any
  `OSError` after `mkstemp`, try once to `os.unlink(tmp)` (ignoring `OSError`), then raise.
- `delete_page`: `ValueError` unless `is_page_id`; `False` if `not folder.exists()`;
  `StorageError` as above if it is a symlink; `shutil.rmtree(folder)`; `True`.
- Every `OSError` is logged with `logger.warning("Pages folder %s failed for %s: %s", operation,
  page_id, exc.strerror)` and re-raised as `StorageError("The page could not be saved in the pages folder.")`
  (write) or `StorageError("The page could not be removed from the pages folder.")` (delete).

### D5. State database: `state.py`

One SQLite file, `<state.dir>/state.sqlite3`, via the standard library.

```python
SCHEMA_VERSION = 1
DB_FILE_NAME = "state.sqlite3"

class StateError(Exception): ...

class StateDB:
    def __init__(self, path: Path) -> None: ...
    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]: ...
    def close(self) -> None: ...
```

- `close()` closes the connection (tests call it directly).
- `__init__` creates the parent folder (`mkdir(parents=True, exist_ok=True, mode=0o700)`),
  connects with `sqlite3.connect(path, check_same_thread=False, isolation_level=None)`, sets
  `PRAGMA journal_mode=WAL` and `PRAGMA foreign_keys=ON`, then reads `PRAGMA user_version`: `0`
  → run the schema below and set it to `1`; `1` → nothing; anything else →
  `StateError(f"{path} has schema version {n}; this version of html-artifact-deploy understands 1.")`.
  After creating the file, `os.chmod(path, 0o600)`.
- `transaction()` holds a `threading.Lock` (an instance attribute), runs `BEGIN IMMEDIATE`,
  yields the connection, `COMMIT`s, or `ROLLBACK`s and re-raises on an exception.
- Rows are read with `connection.row_factory = sqlite3.Row`.

Schema version 1 (every table any later phase needs, so no later phase migrates):

```sql
CREATE TABLE pages (
  page_id TEXT PRIMARY KEY, owner TEXT NOT NULL, title TEXT NOT NULL, slug TEXT NOT NULL,
  bytes INTEGER NOT NULL, sha256 TEXT NOT NULL, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL);
CREATE INDEX pages_by_owner ON pages(owner, updated_at);
CREATE TABLE oauth_clients (client_id TEXT PRIMARY KEY, info_json TEXT NOT NULL, created_at INTEGER NOT NULL);
CREATE TABLE auth_requests (
  state_hash TEXT PRIMARY KEY, client_id TEXT NOT NULL, params_json TEXT NOT NULL,
  nonce TEXT NOT NULL, binding_hash TEXT, expires_at INTEGER NOT NULL);
CREATE TABLE auth_codes (
  code_hash TEXT PRIMARY KEY, client_id TEXT NOT NULL, code_json TEXT NOT NULL,
  hosted_domain TEXT, family TEXT, used_at INTEGER, expires_at INTEGER NOT NULL);
CREATE TABLE tokens (
  token_hash TEXT PRIMARY KEY, kind TEXT NOT NULL CHECK (kind IN ('access', 'refresh', 'api')),
  client_id TEXT NOT NULL, subject TEXT NOT NULL, hosted_domain TEXT, scopes TEXT NOT NULL,
  resource TEXT, family TEXT, name TEXT, used_at INTEGER, expires_at INTEGER NOT NULL,
  created_at INTEGER NOT NULL);
CREATE UNIQUE INDEX api_token_names ON tokens(subject, name) WHERE kind = 'api';
CREATE TABLE uploads (
  upload_hash TEXT PRIMARY KEY, owner TEXT NOT NULL, state TEXT NOT NULL CHECK (state IN ('open', 'filled')),
  bytes INTEGER, expires_at INTEGER NOT NULL);
CREATE INDEX uploads_by_owner ON uploads(owner, state);
```

Secrets (tokens, codes, `state` values, upload ids) are stored only as
`hashlib.sha256(value.encode()).hexdigest()`; `state.py` exports `def secret_hash(value: str) -> str`.

### D6. Ownership: `page_index.py`

```python
@dataclass(frozen=True)
class PageRecord:
    page_id: str
    owner: str
    title: str
    slug: str
    bytes: int
    sha256: str
    created_at: int
    updated_at: int

class PageIndex:
    def __init__(self, db: StateDB) -> None: ...
    def get(self, owner: str, page_id: str) -> PageRecord | None: ...    # None if absent OR owned by someone else
    def list(self, owner: str, limit: int) -> list[PageRecord]: ...       # newest updated_at first, then page_id
    def upsert(self, record: PageRecord) -> None: ...
    def remove(self, owner: str, page_id: str) -> bool: ...
```

The index is what makes a page "yours": listing reads only it, and replacing or unpublishing
refuses a `page_id` whose row has another owner. The pages folder is shared by everyone and is
never listed. `owner` is the lowercased Google e-mail address over HTTP, or `stdio.user` over
stdio.

### D7. Business logic: `service.py`

```python
class PageError(Exception): ...        # its message goes to the caller verbatim

@dataclass(frozen=True)
class PublishResult:
    record: PageRecord
    url: str
    created: bool

class PageService:
    def __init__(self, config: PagesConfig, store: PageStore, index: PageIndex, *, clock: Callable[[], float] = time.time) -> None: ...
    def url_for(self, record: PageRecord) -> str: ...
    async def publish(self, owner: str, title: str, data: bytes, page_id: str = "") -> PublishResult: ...
    async def list_pages(self, owner: str, limit: int) -> list[PageRecord]: ...
    async def unpublish(self, owner: str, page_id: str) -> PageRecord: ...
    async def check_can_publish(self, owner: str, title: str, page_id: str) -> None: ...
```

`publish`, in this order:
1. `title = pages.clean_title(title)`; its `ValueError` → `PageError("title must be 1 to 200 characters.")`.
2. `len(data) == 0` → `PageError("The page is empty.")`.
3. `len(data) > config.max_bytes` → `PageError(f"The page is {len(data):,} bytes, over the {config.max_bytes:,}-byte limit.")`.
4. `data.decode("utf-8")` fails → `PageError("The page is not UTF-8 text, so it is not an HTML page.")`.
5. If `page_id`: `existing = index.get(owner, page_id)`; `None` →
   `PageError(f"No page with page_id {page_id!r} was published by you. Call list_pages to see your pages.")`.
   The slug and `created_at` of `existing` are kept; the title is updated.
   Else: `page_id = new_page_id()`, `slug = slugify(title)`.
6. `await asyncio.to_thread(store.write_page, page_id, slug, data)`; `StorageError` →
   `PageError("The page could not be saved on the server. Try again later, or ask whoever runs html-artifact-deploy to check its log.")`.
7. `index.upsert(...)` (through `to_thread`) with `sha256 = hashlib.sha256(data).hexdigest()`.
8. `logger.info("published page_id=%s owner=%s bytes=%d replaced=%s", ...)`. Never the content or title.

`check_can_publish` runs steps 1 and 5's ownership check only; the upload path (D12) calls it
before reading an upload, so a bad title does not consume the upload.

`unpublish`: `index.get` → `None` gives the same "No page with page_id …" error; then
`store.delete_page` (a `StorageError` →
`PageError("The page could not be removed from the server. Try again later, or ask whoever runs html-artifact-deploy to check its log.")`
and the index is left unchanged); then `index.remove`; log
`"unpublished page_id=%s owner=%s"`; return the removed record. A folder that was already gone
still counts as removed.

`list_pages` clamps `limit` to 1…200.

### D8. MCP tools: `server.py`

```python
SERVER_NAME = "html-artifact-deploy"

@dataclass(frozen=True)
class AppContext:
    service: PageService
    owner: Callable[[], str]          # who is calling; raises ToolError("Not signed in.") if unknown
    uploads: UploadStore | None = None   # None over stdio; the field is added by p7
    fetcher: FetchClient | None = None   # None without [fetch]; the field is added by p8

def build_server(
    app: AppContext,
    *,
    auth_server_provider: OAuthAuthorizationServerProvider[Any, Any, Any] | None = None,
    auth: AuthSettings | None = None,
) -> MCPServer: ...
```

`echo` is removed. `server_info` stays as it is. Every tool catches `PageError` (and from p7
`UploadError`, from p8 `FetchClientError`) and raises `ToolError(str(exc))`; nothing else is caught (the SDK turns other
exceptions into a generic error and logs the traceback).

Tools, with their exact descriptions (the function docstring is the description; `Args:` documents
parameters as `server.py` does today):

**`publish_page(title: str, html: str = "", upload_id: str = "", page_id: str = "") -> dict[str, object]`**
annotations `ToolAnnotations(title="Publish HTML page", readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)`

> Publish a single-file HTML page on your organization's page server and return its link.
>
> Pass the whole HTML document as html, with CSS and scripts inline or loaded from public CDNs.
> For a page over about 100 KB, when create_page_upload is available, upload the file first and
> pass upload_id instead. Returns page_id, url, title, bytes, created (false when an existing page
> was replaced) and updated_at. Give url to the user: anyone with that link can open the page. To
> change a page you published, call this again with its page_id; the page is replaced and keeps its
> url. Use list_pages to find a page_id, and unpublish_page to take a page down.
>
> Args:
>     title: What the page is, in words, for example "Q3 report". 1 to 200 characters. It also
>         names the file in the link (q3-report.html); a replacement keeps the original link.
>     html: The complete HTML document. Give exactly one of html and upload_id.
>     upload_id: The upload_id from create_page_upload, after the file was PUT to its upload_url.
>         Empty when html is given.
>     page_id: The page_id of a page you published, to replace it. Empty to publish a new page.

Flow: exactly one of `html`/`upload_id` non-empty, else `ToolError("Give exactly one of html and upload_id.")`.
With `upload_id` and `app.uploads is None`: `ToolError("Uploads are not available on this connection; pass the page as html.")`.
With `html`: `service.publish(owner, title, html.encode("utf-8"), page_id)`.
With `upload_id` (p7): `await service.check_can_publish(owner, title, page_id)`, then
`data = await uploads.read(owner, upload_id)`, then `service.publish(...)`, then
`await uploads.discard(upload_id)`.
Result: `{"page_id", "url", "title", "bytes", "created", "updated_at"}` (ISO string).

p8 inserts one sentence after "pass upload_id instead.": `If the page is already online,
publish_page_from_url (when available) fetches it from its address instead.`

**`list_pages(limit: int = 50) -> dict[str, object]`**
annotations `ToolAnnotations(title="List my HTML pages", readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)`

> List the HTML pages you published, newest first.
>
> Returns pages (each with page_id, title, url, bytes, created_at and updated_at) and count. Only
> your own pages are listed. Use a page_id with publish_page to replace that page, or with
> unpublish_page to take it down.
>
> Args:
>     limit: The most pages to return, 1 to 200. Defaults to 50.

**`unpublish_page(page_id: str) -> dict[str, object]`**
annotations `ToolAnnotations(title="Take down HTML page", readOnlyHint=False, destructiveHint=True, idempotentHint=True, openWorldHint=True)`

> Take down an HTML page you published: delete it from the page server so its link stops working.
>
> This cannot be undone; publishing the page again gives it a new link. Returns page_id, url and
> removed. Use list_pages to find the page_id.
>
> Args:
>     page_id: The page_id of one of your pages, from publish_page or list_pages.

Result `{"page_id", "url", "removed": True}`.

**`create_page_upload() -> dict[str, object]`** (registered only when `app.uploads` is not `None`; p7)
annotations `ToolAnnotations(title="Create page upload URL", readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=False)`

> Create a one-time URL to upload a large HTML page to, for publish_page.
>
> Returns upload_id, upload_url, method (always PUT), max_bytes and expires_at. Send the file's
> bytes as the body of an HTTP PUT to upload_url within 15 minutes, for example
> `curl --upload-file page.html "<upload_url>"`, then call publish_page with upload_id and no html.
> Only useful when you can make HTTP requests or run commands (for example in Claude Code); otherwise
> pass the page to publish_page as html.

### D9. Google sign-in client: `google_oidc_client.py`

```python
GOOGLE_AUTHORIZATION_ENDPOINT = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_ENDPOINT = "https://oauth2.googleapis.com/token"  # nosec B105  # a public URL
GOOGLE_ISSUERS = ("https://accounts.google.com", "accounts.google.com")
TIMEOUT_SECONDS = 10

class GoogleOidcClientError(Exception): ...   # message is generic and safe to show a person

@dataclass(frozen=True)
class GoogleIdentity:
    email: str              # lowercased
    hosted_domain: str | None   # the `hd` claim
    subject: str            # the `sub` claim

class GoogleOidcClient:
    def __init__(self, client_id: str, client_secret: str, *, clock: Callable[[], float] = time.time) -> None: ...
    def authorization_url(self, state: str, nonce: str, redirect_uri: str) -> str: ...
    async def exchange_code(self, code: str, redirect_uri: str, nonce: str) -> GoogleIdentity: ...
    def _post_token_request(self, form: dict[str, str]) -> dict[str, Any]: ...   # blocking; tests stub it
```

- `authorization_url`: `GOOGLE_AUTHORIZATION_ENDPOINT + "?" + urlencode({"response_type": "code",
  "client_id", "redirect_uri", "scope": "openid email", "state", "nonce", "prompt": "select_account"})`.
- `exchange_code`: `await asyncio.to_thread(self._post_token_request, {"grant_type":
  "authorization_code", "code", "redirect_uri", "client_id", "client_secret"})`.
  `_post_token_request` uses `urllib.request.Request(GOOGLE_TOKEN_ENDPOINT, data=urlencode(form).encode(),
  method="POST", headers={"Content-Type": "application/x-www-form-urlencoded"})` and
  `urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS)` (`# nosec B310  # fixed https Google endpoint`),
  parses JSON.
- The `id_token` is decoded, **without signature verification**: split on `.`, exactly three
  parts, base64url-decode the middle one (pad with `=`), `json.loads`. OpenID Connect Core 1.0
  §3.1.3.7 allows TLS server validation in place of the signature when the ID token comes
  directly from the token endpoint over TLS, which is the only way this client gets one. Checks:
  `iss in GOOGLE_ISSUERS`; `aud == client_id`; `exp > clock()`; `nonce == nonce`; `email` is a
  non-empty string; `email_verified is True`.
- Every failure (one `except OSError` clause covers `urllib.error.URLError`, `TimeoutError` and
  socket errors, which all subclass it; a `ValueError` from non-JSON or bad base64; a missing
  `id_token`; a failed check) is logged with `logger.warning("Google sign-in failed: %s", reason)`
  (`reason` is a short fixed description such as `"nonce mismatch"` or the exception's class name,
  never a token or code) and raised as `GoogleOidcClientError("Google sign-in failed.")`.

Live check (layer 5, needs no credentials): `scripts/live_check.py` gains
`CHECKS["google_openid_configuration"] = LiveCheck(fixture="openid-configuration.json", fetch=_fetch_google_openid_configuration, redact=_keep_google_endpoints)`.
`fetch` GETs `https://accounts.google.com/.well-known/openid-configuration` with `urllib` (timeout
10, `# nosec B310  # fixed https Google endpoint`); `redact` returns the dict restricted to the keys
`issuer`, `authorization_endpoint`, `token_endpoint`, `response_types_supported`,
`scopes_supported`, `claims_supported`. The fixture is committed at
`tests/fixtures/live/google_openid_configuration/openid-configuration.json`.

### D10. OAuth authorization server: `token_store.py`, `oauth_provider.py`

The server is its own OAuth 2.1 authorization server, as the MCP authorization spec expects and
claude.ai requires (dynamic client registration, PKCE, metadata, all served by the SDK's auth
routes: `/.well-known/oauth-authorization-server`, `/.well-known/oauth-protected-resource/mcp`,
`/register`, `/authorize`, `/token`, `/revoke`). It never hands Google's tokens to clients: Google
only proves who the person is, and this server issues its own opaque tokens.

Constants (`oauth_provider.py` unless noted): token prefixes `ACCESS_PREFIX = "hda_"`,
`REFRESH_PREFIX = "hdr_"`, `API_PREFIX = "hdk_"` (`token_store.py`; each `# nosec B105  # a token
prefix, not a secret`), each followed by `secrets.token_urlsafe(32)`; authorization codes,
`state` values and sign-in binding values are `secrets.token_urlsafe(32)`. Lifetimes:
`ACCESS_TOKEN_TTL = 3600`, `REFRESH_TOKEN_TTL = 30 * 86400` (absolute: a rotated refresh token
keeps the expiry of the one it replaces, so a person signs in with Google at least every 30 days),
`AUTH_CODE_TTL = 300`, `AUTH_REQUEST_TTL = 600`. Scope: the single scope `SCOPE = "pages"`.
`API_TOKEN_CLIENT_ID = "api-token"` (`# nosec B105  # a client id label`).
`MAX_PENDING_SIGN_INS = 1000`. `BINDING_COOKIE = "__Host-hda_signin"` when `http.public_url` is
https, else `"hda_signin"` (tests on `http://127.0.0.1`).

`TokenStore(db: StateDB, *, clock=time.time)`, all methods synchronous. Every stored secret is a
`secret_hash`; `expires_at <= now` means absent.

```python
def save_client(self, info: OAuthClientInformationFull) -> None
def get_client(self, client_id: str) -> OAuthClientInformationFull | None
def count_pending_sign_ins(self) -> int
def save_auth_request(self, state: str, client_id: str, params: AuthorizationParams, nonce: str) -> None
def get_auth_request(self, state: str) -> tuple[str, AuthorizationParams, str, str | None] | None   # as pop_auth_request, without deleting
def bind_auth_request(self, state: str, binding: str) -> bool                  # sets binding_hash once; False if absent, expired or already bound
def pop_auth_request(self, state: str) -> tuple[str, AuthorizationParams, str, str | None] | None
    # (client_id, params, nonce, binding_hash); deletes the row; None if absent or expired
def save_code(self, code: AuthorizationCode, hosted_domain: str | None) -> None
def get_code(self, client_id: str, code: str) -> tuple[AuthorizationCode, str | None, str | None] | None
    # (code, hosted_domain, used_family): None if absent, other client or expired; used_family is the
    # family issued from it if it was already exchanged, else None
def mark_code_used(self, code: str, family: str) -> bool                        # UPDATE ... WHERE used_at IS NULL; True if this call marked it
def issue(self, kind: Literal["access", "refresh"], client_id: str, subject: str, hosted_domain: str | None,
          scopes: list[str], resource: str | None, family: str, expires_at: int) -> str
def get_token(self, token: str, kinds: tuple[str, ...]) -> sqlite3.Row | None   # None if absent or expired (used rows ARE returned)
def mark_used(self, token: str) -> bool                                         # UPDATE ... WHERE used_at IS NULL; True if this call marked it
def revoke_family(self, family: str) -> None                                    # deletes every token row of the family
def create_api_token(self, subject: str, name: str, days: int) -> str
    # subject lowercased; kind 'api', client_id API_TOKEN_CLIENT_ID, scopes "pages", resource NULL,
    # hosted_domain = subject's domain, expires_at = now + days*86400;
    # sqlite3.IntegrityError on a duplicate -> ValueError(f"{subject} already has an API token named {name!r}.")
def list_api_tokens(self) -> list[tuple[str, str, int, int]]                   # (subject, name, expires_at, created_at)
def revoke_api_token(self, subject: str, name: str) -> bool
def purge_expired(self) -> None
    # deletes expired auth_requests, auth_codes and tokens (used or not), and oauth_clients older
    # than 30 days that no token row names
```

`AuthorizationParams`, `AuthorizationCode` and clients are stored with `model_dump_json()` and read
back with `model_validate_json()`.

`GoogleOAuthProvider(auth: AuthConfig, http: HttpConfig, store: TokenStore, google: GoogleOidcClient, *, clock=time.time)`
implements `OAuthAuthorizationServerProvider[AuthorizationCode, RefreshToken, AccessToken]` with
the SDK's exact signatures (every store call through `asyncio.to_thread`):

- `START_PATH = "/oauth/google/start"`, `CALLBACK_PATH = "/oauth/google/callback"`;
  `callback_url = http.public_url + CALLBACK_PATH`; `resource_url = http.public_url + "/mcp"`.
- `is_allowed_redirect_uri(uri: str, allowlist: Sequence[str]) -> bool` (module function): `True`
  if `uri` is exactly in the allowlist, or if `urlsplit(uri)` has scheme `http` and hostname in
  `{"localhost", "127.0.0.1", "::1"}` (Claude Code's local callback, any port and path).
- `register_client(info)`: every `info.redirect_uris` entry must pass `is_allowed_redirect_uri`,
  else `RegistrationError("invalid_redirect_uri", "This server accepts only the redirect URIs of claude.ai, claude.com and local (loopback) addresses. Ask whoever runs it to add yours to auth.redirect_uri_allowlist.")`.
  Then `store.save_client`. The allowlist is what stops a stranger's client from receiving a
  signed-in person's code at their own site (ADR 0015).
- `authorize(client, params)`:
  1. `is_allowed_redirect_uri(str(params.redirect_uri), ...)` false →
     `AuthorizeError("invalid_request", "This redirect URI is no longer allowed.")`.
  2. `params.resource not in (None, resource_url)` →
     `AuthorizeError("invalid_target", "This server only issues tokens for its own /mcp endpoint.")`.
  3. `store.purge_expired()`; `count_pending_sign_ins() >= MAX_PENDING_SIGN_INS` →
     `AuthorizeError("temporarily_unavailable", "Too many sign-ins are in progress; try again in a few minutes.")`.
  4. `state`, `nonce` = `token_urlsafe(32)`; `save_auth_request(...)`; return
     `f"{http.public_url}{START_PATH}?{urlencode({'state': state})}"`.
- `async handle_google_start(request) -> Response` (custom route `GET START_PATH`): this binds the
  sign-in to the browser that began it, so a Google link copied out of someone else's sign-in
  cannot finish it. `binding = token_urlsafe(32)`; `bind_auth_request(state, binding)` false →
  the 400 "expired" page below. Else 302 to `google.authorization_url(state, nonce, callback_url)`
  (the nonce read with `get_auth_request(state)`) with `Set-Cookie: <BINDING_COOKIE>=<binding>; Path=/; HttpOnly; SameSite=Lax; Max-Age=600`
  plus `; Secure` on https.
- `async handle_google_callback(request) -> Response` (custom route `GET CALLBACK_PATH`):
  1. `pop_auth_request(query "state")`; `None` →
     `HTMLResponse(page("This sign-in link has expired or was already used. Start again from your AI client."), 400)`.
  2. The request's `BINDING_COOKIE` value, hashed, must equal the row's `binding_hash` (and the
     hash must not be `None`), compared with `hmac.compare_digest`; else
     `HTMLResponse(page("This sign-in was started in a different browser. Start again from your AI client."), 400)`.
  3. If the query has `error` (the person cancelled at Google): 302 to
     `construct_redirect_uri(str(params.redirect_uri), error="access_denied", state=params.state)`.
  4. `identity = await google.exchange_code(query "code", callback_url, nonce)`; `GoogleOidcClientError` →
     `HTMLResponse(page("Google sign-in failed. Start again from your AI client."), 502)`.
  5. `is_allowed_email(auth, identity.email, identity.hosted_domain)` false →
     `HTMLResponse(page(f"{html.escape(identity.email)} may not publish pages on this server. Ask whoever runs it to add your address or domain."), 403)`.
  6. `code = token_urlsafe(32)`; `AuthorizationCode(code=code, scopes=params.scopes or [SCOPE],
     expires_at=clock() + AUTH_CODE_TTL, client_id=client_id, code_challenge=params.code_challenge,
     redirect_uri=params.redirect_uri, redirect_uri_provided_explicitly=params.redirect_uri_provided_explicitly,
     resource=params.resource, subject=identity.email)`; `save_code(code, identity.hosted_domain)`;
     log `logger.info("signed in %s for client %s", identity.email, client_id)`; 302 to
     `construct_redirect_uri(str(params.redirect_uri), code=code, state=params.state)`, deleting the
     binding cookie (`Max-Age=0`).
  Every response here carries `Cache-Control: no-store`. `page(message)` is
  `<!doctype html><meta charset="utf-8"><title>html-artifact-deploy</title><p>{message}</p>`.
- `load_authorization_code(client, code)`: `get_code`; `None` → `None`; if `used_family` is set (a
  replayed code) → `revoke_family(used_family)` and return `None` (RFC 6749 section 4.1.2).
- `exchange_authorization_code(client, code)`: `family = token_urlsafe(16)`;
  `mark_code_used(code.code, family)` false (lost a race with a concurrent exchange) →
  `TokenError("invalid_grant", "The authorization code was already used.")`; issue an access token
  (`expires_at = now + ACCESS_TOKEN_TTL`) and a refresh token (`now + REFRESH_TOKEN_TTL`), both
  with `subject = code.subject`, the stored `hosted_domain`, `resource = code.resource`; return
  `OAuthToken(access_token=…, token_type="Bearer", expires_in=ACCESS_TOKEN_TTL, scope=" ".join(code.scopes), refresh_token=…)`
  (`# nosec B106  # the OAuth token type, not a password`).
- `load_refresh_token(client, token)`: `get_token(token, ("refresh",))`; `None` or another
  `client_id` → `None`; `used_at` set (a replayed, already-rotated token) → `revoke_family` and
  `None`; subject no longer allowed (`is_allowed_email(auth, subject, hosted_domain)` false) →
  `revoke_family` and `None`; else `RefreshToken(token, client_id, scopes, expires_at, resource, subject)`.
- `exchange_refresh_token(client, refresh_token, scopes)`: `mark_used(refresh_token.token)` false
  (lost a race) → `revoke_family` and `TokenError("invalid_grant", "The refresh token was already used.")`;
  else issue a new access token and a new refresh token in the same family, the refresh token
  keeping the old row's `expires_at`, with `scopes or refresh_token.scopes`. (The SDK itself
  rejects scopes that were not granted before calling this, so there is no scope check here.)
- `load_access_token(token)`: `get_token(token, ("access", "api"))`; `None` → `None`; subject no
  longer allowed → `None` (and for kind `access`, `revoke_family`); else `AccessToken(token=token,
  client_id=row["client_id"], scopes=row["scopes"].split(), expires_at=row["expires_at"],
  resource=row["resource"] or resource_url, subject=row["subject"])`.
- `revoke_token(token)`: `token` is an `AccessToken` or `RefreshToken`; look up its row and
  `revoke_family(row["family"])` (both kinds carry the family); an API token is not revocable here
  (only `token revoke` on the command line).

Flow tests through the SDK routes use the real clock (the SDK's token handler and bearer
middleware read `time.time()`); expiry tests call the provider and store directly with an
injected clock. Race branches (`mark_code_used` false, `mark_used` false) are covered by direct
calls that mark the row first.

`def auth_settings(http: HttpConfig) -> AuthSettings` (in `oauth_provider.py`) returns
`AuthSettings(issuer_url=http.public_url, resource_server_url=http.public_url + "/mcp",
service_documentation_url=None, client_registration_options=ClientRegistrationOptions(enabled=True,
valid_scopes=[SCOPE], default_scopes=[SCOPE]), revocation_options=RevocationOptions(enabled=True),
required_scopes=[SCOPE], validate_token_resource=True)`.

### D11. HTTP app: `http_app.py`

```python
MCP_PATH = "/mcp"
HEALTH_PATH = "/healthz"

def max_request_body_size(max_bytes: int) -> int:   # max(DEFAULT_MAX_REQUEST_BODY_SIZE, 2 * max_bytes + 1_048_576)
def build_http_app(config: Config, *, environ: Mapping[str, str] = os.environ, google: GoogleOidcClient | None = None, clock: Callable[[], float] = time.time) -> Starlette: ...
```

`build_http_app(config, *, environ=os.environ, google=None, clock=time.time)`: `http, auth = require_http(config)`; `db = StateDB(config.state_dir / DB_FILE_NAME)`;
`store = LocalFolderStore(config.pages.root)`; `store.check()`; `service`; `tokens = TokenStore(db)`;
`google = google or GoogleOidcClient(auth.google_client_id, google_client_secret(auth, environ))`;
`provider = GoogleOAuthProvider(...)`; `owner` = a function that returns
`get_access_token().subject` and raises `ToolError("Not signed in.")` if the token or its subject
is `None`; `server = build_server(AppContext(service, owner, uploads), auth_server_provider=provider, auth=<D10 settings>)`;
`server.custom_route(START_PATH, ["GET"])(provider.handle_google_start)`;
`server.custom_route(CALLBACK_PATH, ["GET"])(provider.handle_google_callback)`;
`server.custom_route(HEALTH_PATH, ["GET"])` returning `PlainTextResponse("ok")`; then (every
`custom_route` call must come before this one: the SDK copies the route list when it builds the app)
`server.streamable_http_app(streamable_http_path=MCP_PATH, json_response=True, stateless_http=True,
max_request_body_size=max_request_body_size(config.pages.max_bytes),
transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=True,
allowed_hosts=[<public host>, <public host>:*, "127.0.0.1:*", "localhost:*"],
allowed_origins=[http.public_url, "https://claude.ai", "https://claude.com", "http://127.0.0.1:*", "http://localhost:*"]), host=http.listen_host)`.
`<public host>` is `urlsplit(http.public_url).netloc`. The SDK's `TransportSecurityMiddleware`
accepts the `host:*` port wildcard (`transport_security.py:_validate_host`). Because Host is
checked, route tests must send requests with `base_url=http.public_url`, not `http://testserver`.

Stateless JSON responses: no MCP session lives in the process, so a restart or a second request
loses nothing, and there is no server-to-client stream to keep open behind the reverse proxy.

`serve-http` runs `uvicorn.run(app, host=http.listen_host, port=http.listen_port,
proxy_headers=True, forwarded_allow_ips="127.0.0.1", log_level="info", access_log=False)`
(no access log: request lines would carry upload URLs and Google's `code`, which are secrets) inside
`def _run_uvicorn(app: Starlette, http: HttpConfig) -> None:  # pragma: no cover` in `__main__.py`
(its one line runs only in the integration test's subprocess, which coverage does not see).

### D12. Uploads: `uploads.py`

```python
UPLOAD_TTL = 900
UPLOAD_PATH = "/uploads/{upload_id}"

class UploadError(Exception): ...     # message goes to the caller verbatim

@dataclass(frozen=True)
class UploadSlot:
    upload_id: str
    upload_url: str
    max_bytes: int
    expires_at: int

class UploadStore:
    def __init__(self, db: StateDB, folder: Path, public_url: str, max_bytes: int, *, clock=time.time) -> None: ...
    async def create(self, owner: str) -> UploadSlot: ...
    async def handle_put(self, request: Request) -> Response: ...
    async def read(self, owner: str, upload_id: str) -> bytes: ...
    async def discard(self, upload_id: str) -> None: ...
```

- `folder` is `config.state_dir / "uploads"`, created with mode `0o700`. A file is
  `folder / secret_hash(upload_id)`.
- `MAX_OPEN_UPLOADS = 10` per owner.
- `create`: purge expired rows and their files, and `*.part` files older than `UPLOAD_TTL`; if the
  owner has `MAX_OPEN_UPLOADS` unexpired rows →
  `UploadError(f"You have {MAX_OPEN_UPLOADS} uploads waiting. Publish them, or wait 15 minutes for them to expire.")`;
  `upload_id = secrets.token_urlsafe(32)`; insert
  `(secret_hash, owner, 'open', NULL, now + UPLOAD_TTL)`; `upload_url = f"{public_url}/uploads/{upload_id}"`.
- `handle_put` (route `PUT /uploads/{upload_id}`, no bearer token: the URL is the capability):
  unknown, expired or `filled` → `PlainTextResponse("Unknown or expired upload.", 404)`;
  `Content-Length` header over `max_bytes` → `PlainTextResponse(f"The file is over the {max_bytes:,}-byte limit.", 413)`;
  stream `request.stream()` into a temporary file from
  `tempfile.mkstemp(dir=folder, suffix=".part")` (so two concurrent PUTs never share a file), counting bytes, and stop with the same 413 (and
  delete the part file) as soon as the count exceeds `max_bytes`; empty body →
  `PlainTextResponse("The upload is empty.", 400)`; else mark the row `filled` with
  `UPDATE … WHERE state = 'open'` (if no row changed, a concurrent PUT won: delete the part file and
  answer the 404 above), then `os.replace` to the final name, `PlainTextResponse(f"Uploaded {n:,} bytes. Now call publish_page with this upload_id.", 201)`.
- `read(owner, upload_id)`: the row must exist, be `filled`, not expired, and have this `owner`,
  else `UploadError("No uploaded file for that upload_id. Call create_page_upload, PUT the file to its upload_url, then call publish_page again.")`.
  Returns the file's bytes.
- `discard(upload_id)`: delete the row and the file (missing file ignored).

`build_http_app` builds `UploadStore(db, config.state_dir / "uploads", http.public_url,
config.pages.max_bytes)` and registers `server.custom_route(UPLOAD_PATH, ["PUT"])(uploads.handle_put)` before calling
`streamable_http_app`.

### D13. Command line: `__main__.py`

```
html-artifact-deploy [--config PATH] [--version]           stdio server (the default, as today)
html-artifact-deploy [--config PATH] serve-http             Streamable HTTP server with sign-in
html-artifact-deploy [--config PATH] check-config           validate the file and the folders, print "Configuration OK: <path>"
html-artifact-deploy [--config PATH] token create --user EMAIL --name NAME [--days N]
html-artifact-deploy [--config PATH] token list
html-artifact-deploy [--config PATH] token revoke --user EMAIL --name NAME
```

- `--config` is a top-level option (before the subcommand). `main(argv=None, environ=None) -> int`
  (`environ` defaults to `os.environ`).
- Stdio (no subcommand): load config, `LocalFolderStore.check()`, build `AppContext(service,
  owner=lambda: config.stdio_user, uploads=None)` and run `build_server(...).run("stdio")`. It prints
  nothing on stdout.
- Every `ConfigError`, `StorageError` or `StateError` prints `html-artifact-deploy: <message>` on
  **stderr** and returns `2`.
- `token create`: `--user` is lowercased; `--days` default `365`, 1…3650; the address must pass
  `is_allowed_email(auth, email, email.rpartition("@")[2])`, else stderr
  `html-artifact-deploy: <email> is not in auth.allowed_emails or auth.allowed_domains.` and `2`.
  Prints only the token on stdout, then on stderr
  `Store this token now; it is not shown again. Use it as: Authorization: Bearer <token>`.
- `token list` prints one tab-separated line per API token: `subject  name  expires(ISO)  created(ISO)`.
- `token revoke` prints `Revoked.` or, if nothing matched, stderr `html-artifact-deploy: no such token.` and `1`.
- `token` and `serve-http` need `[http]` and `[auth]` (`require_http`); only `serve-http` and
  `check-config` read the client secret (D2).
- On a server, every command runs as the service user, so files in `state.dir` keep one owner:
  `sudo -u html-artifact-deploy /opt/html-artifact-deploy/bin/html-artifact-deploy --config /etc/html-artifact-deploy/config.toml token create …`.
  `check-config` needs the secret too, so the guide runs it through
  `sudo systemd-run --pty --wait --uid=html-artifact-deploy -p EnvironmentFile=/etc/html-artifact-deploy/secrets.env …`.

### D14. Deployment (documentation and example files)

Target: one Ubuntu 24.04 machine with Caddy (automatic HTTPS), two DNS names pointing at it:
`publish.example.com` (this server, reverse-proxied) and `pages.example.com` (the pages folder).

Files under `deploy/`:
- `deploy/config.example.toml` — the D2 example with comments (p1).
- `deploy/html-artifact-deploy.service` — systemd unit: `User=html-artifact-deploy`,
  `Group=html-artifact-deploy`, `ExecStart=/opt/html-artifact-deploy/bin/html-artifact-deploy --config /etc/html-artifact-deploy/config.toml serve-http`,
  `EnvironmentFile=/etc/html-artifact-deploy/secrets.env`, `StateDirectory=html-artifact-deploy`,
  `ReadWritePaths=/srv/pages`, `NoNewPrivileges=yes`, `ProtectSystem=strict`, `ProtectHome=yes`,
  `PrivateTmp=yes`, `PrivateDevices=yes`, `RestrictAddressFamilies=AF_INET AF_INET6 AF_UNIX`,
  `Restart=on-failure`, `UMask=0022`.
- `deploy/Caddyfile.example`:

```
publish.example.com {
    reverse_proxy 127.0.0.1:8765
}

pages.example.com {
    root * /srv/pages
    file_server
    header {
        Content-Security-Policy "sandbox allow-scripts allow-popups allow-popups-to-escape-sandbox allow-forms allow-modals allow-downloads"
        X-Content-Type-Options nosniff
        Referrer-Policy no-referrer
        X-Robots-Tag "noindex, nofollow"
        Cache-Control no-cache
    }
}
```

`file_server` without `browse` lists no directories, so the random folder name keeps other
pages from being found.

`docs/deployment.md` (p9), in order: 1. DNS; 2. Google OAuth client (Google Auth Platform: Branding, then Audience
set to **Internal**, then Clients → Create client → **Web application**, authorized redirect URI `https://publish.example.com/oauth/google/callback`);
3. `sudo apt install caddy` (Caddy's apt repository), `sudo useradd --system --home-dir /var/lib/html-artifact-deploy --shell /usr/sbin/nologin html-artifact-deploy`,
`sudo install -d -o html-artifact-deploy -g html-artifact-deploy -m 755 /srv/pages`;
4. `sudo python3 -m venv /opt/html-artifact-deploy && sudo /opt/html-artifact-deploy/bin/pip install html-artifact-deploy`
(or `git+https://github.com/andras-tkcs/html-artifact-deploy@<ref>` before the first release);
5. `/etc/html-artifact-deploy/config.toml` (`root:html-artifact-deploy`, `0640`) and
`/etc/html-artifact-deploy/secrets.env` (`root:root`, `0600`) holding
`HTML_ARTIFACT_DEPLOY_GOOGLE_CLIENT_SECRET=…`; 6. `check-config` as the service user; 7. the
systemd unit, `systemctl enable --now`; 8. the Caddyfile, `systemctl reload caddy`; 9. checks:
`curl -s https://publish.example.com/healthz` prints `ok`, `curl -sI https://pages.example.com/`
shows `404` and the `Content-Security-Policy: sandbox …` header; 10. connect clients (D15).
It notes: the sandbox header gives every page an opaque origin, so `localStorage` and cookies
don't persist, and claude.ai-only artifact features (shared storage, asking Claude) do not work
once a page is published elsewhere.

`docs/configuration.md` (p9): every key of D2 with its type, default and meaning, and the D13
commands.

### D15. Connecting clients (README, p9)

- **claude.ai and Claude Desktop**: Settings → Connectors → Add custom connector, URL
  `https://publish.example.com/mcp`; sign in with Google when asked.
- **Claude Code, OAuth**: `claude mcp add --transport http html-artifact-deploy https://publish.example.com/mcp`,
  then `/mcp` to sign in.
- **Claude Code, API token**: an administrator runs `token create --user you@example.com --name laptop` as the service user (D13);
  then `claude mcp add --transport http html-artifact-deploy https://publish.example.com/mcp --header "Authorization: Bearer <token>"`.
- **Local, stdio** (development and trying it out, with a config whose `pages.root` and
  `state.dir` are folders you own): `claude mcp add html-artifact-deploy -- html-artifact-deploy --config ~/html-artifact-deploy.toml`.
  Not for a server deployment, whose folders belong to the service user.

### D16. Publishing from an https address: `fetch_client.py`, `publish_page_from_url`

For a page that already lives somewhere Claude cannot copy it from cheaply (a Git host's raw file,
a presigned object-storage URL, an intranet file server reachable from this machine), the server
downloads it itself. Off by default: without a `[fetch]` section the tool is not registered. Hosts
are an explicit list of names written by the administrator (D2), because a server that fetches any
URL a model hands it can be steered at internal services (server-side request forgery).

```python
USER_AGENT = f"html-artifact-deploy/{__version__}"
MAX_REDIRECTS = 3
CHUNK = 65_536

class FetchClientError(Exception): ...          # message goes to the caller verbatim

class FetchClient:
    def __init__(self, config: FetchConfig, max_bytes: int, *,
                 resolve: Callable[..., list[tuple[Any, ...]]] = socket.getaddrinfo,
                 handlers: Sequence[urllib.request.BaseHandler] = ()) -> None: ...
    async def fetch(self, url: str) -> bytes: ...          # asyncio.to_thread(self._fetch_blocking, url)
    def check_url(self, url: str) -> str: ...              # returns the lowercased host, or raises
    def _fetch_blocking(self, url: str) -> bytes: ...
```

The opener is `urllib.request.build_opener(urllib.request.ProxyHandler({}), _AllowlistRedirectHandler(self), *handlers)`.
`ProxyHandler({})` turns off proxies from `HTTPS_PROXY` and friends: a proxy would resolve the
name itself (making the address check meaningless) and could add proxy credentials. A deployment
that can reach the internet only through a proxy cannot use this tool; ADR 0020 accepts that.
`handlers` exists for tests: a test passes a `urllib.request.HTTPSHandler` subclass whose
`https_open` builds `resp = urllib.response.addinfourl(io.BytesIO(body), headers, req.full_url, code)`
(`headers` an `email.message.Message`), sets `resp.msg = "OK"` (urllib's `HTTPErrorProcessor`
reads it) and returns it; `build_opener` replaces the default HTTPS handler with it. No cookie
processor or `Authorization` header is ever added.

`check_url(url)`, checks in this order (each raises `FetchClientError` with this message):

| Check | Message |
|---|---|
| any character with code ≤ 0x20 or 0x7F in `url`; `urlsplit(url)` or its `.port` raising `ValueError`; scheme not `https`; no hostname; `username is not None or password is not None`; port not `None` or `443` | `url must be an https:// address with no user name, password or port, for example https://raw.githubusercontent.com/org/repo/main/page.html.` |
| host (lowercased) not in `config.allowed_hosts` | `This server only fetches pages from {', '.join(allowed_hosts)}. Ask whoever runs it to add {host} to fetch.allowed_hosts, or pass the page as html.` |
| `resolve(host, 443, type=socket.SOCK_STREAM)` raises `OSError` | `Could not look up {host}.` |
| any resolved address (`info[4][0]` of each `getaddrinfo` tuple, a `%scope` suffix removed) for which `not ipaddress.ip_address(addr).is_global`, or that lies in `64:ff9b::/96` or `64:ff9b:1::/48` (NAT64, which maps to IPv4 addresses `is_global` cannot see) | `{host} resolves to a private or local address, so this server will not fetch from it.` |

The address check guards against an allowed name whose DNS points inside the network; the
connection resolves the name again, and that window is accepted because only administrator-listed
names reach it (ADR 0020).

`_fetch_blocking(url)`:
1. `host = check_url(url)`.
2. `urllib.request.Request(url, method="GET", headers={"User-Agent": USER_AGENT, "Accept": "text/html, */*;q=0.1"})`;
   `opener.open(request, timeout=config.timeout_seconds)` (bandit does not flag `opener.open`, so no
   `nosec`). The `Request` gets `url` unchanged: `check_url` has already refused control characters,
   so urllib and `urlsplit` see the same address.
3. `_AllowlistRedirectHandler(urllib.request.HTTPRedirectHandler)` overrides
   `redirect_request(self, req, fp, code, msg, headers, newurl)`:
   `len(getattr(req, "redirect_dict", {})) >= MAX_REDIRECTS` →
   `FetchClientError("The address redirected too many times.")`; then `client.check_url(newurl)`,
   whose `FetchClientError` is re-raised as
   `FetchClientError(f"The address redirected to {urlsplit(newurl).hostname or 'an invalid address'}, which this server does not fetch from.")`;
   otherwise return `super().redirect_request(...)`. (urllib calls `redirect_request` before its own
   loop check and keeps `redirect_dict` on the request; it counts distinct URLs, so a loop A→B→A is
   stopped by urllib's own `max_repeats` as an `HTTPError` with the 3xx code, which step 4 reports
   as "answered HTTP 302.". urllib also refuses a `file:` or `data:` Location before calling
   `redirect_request`, the same way.)
4. `urllib.error.HTTPError` (any non-2xx after redirects) → close it (`exc.close()`), then
   `FetchClientError(f"{host} answered HTTP {code}.")`; `OSError` (covers `URLError`, `TimeoutError`,
   TLS errors) or `ValueError` (urllib's own parse of a malformed `Location`) →
   `FetchClientError(f"Could not fetch the page from {host}.")`.
   `FetchClientError` raised inside the handler passes through unchanged.
5. A `Content-Length` header that is all digits (`str.isdigit()`) and over `max_bytes` → `FetchClientError(f"The page at that address is over the {max_bytes:,}-byte limit.")`
   before reading; otherwise read `CHUNK`-sized pieces and stop with the same error as soon as more
   than `max_bytes` have arrived. A `Content-Length` that is not all digits is ignored (the
   streaming limit still applies). The response is closed in a `finally`.
6. `logger.info("fetched %d bytes from %s", len(data), host)`. The URL's path and query are never
   logged or put in an error message (a presigned URL's query is a credential).

Tool, registered by `build_server` only when `app.fetcher is not None`:

**`publish_page_from_url(title: str, url: str, page_id: str = "") -> dict[str, object]`**
annotations `ToolAnnotations(title="Publish HTML page from URL", readOnlyHint=False, destructiveHint=False, idempotentHint=False, openWorldHint=True)`

> Publish a single-file HTML page that is already online, by its https address, and return its link.
>
> The server downloads the file itself, so the page does not have to pass through this
> conversation; use it for large pages. The address must be on a host this server allows (an error
> names them) and must return the file to a plain GET with no sign-in, for example a raw file on a
> Git host or a presigned storage link. Returns page_id, url, title, bytes, created and updated_at,
> as publish_page does. To change a page you published, pass its page_id; the page is replaced and
> keeps its url. For a page you have as text, use publish_page.
>
> Args:
>     title: What the page is, in words, for example "Q3 report". 1 to 200 characters. It also
>         names the file in the link; a replacement keeps the original link.
>     url: The https address of the HTML file.
>     page_id: The page_id of a page you published, to replace it. Empty to publish a new page.

Flow: `await service.check_can_publish(owner, title, page_id)` (so a bad title or a foreign page_id
costs no download), `data = await fetcher.fetch(url)`, `service.publish(owner, title, data, page_id)`
(its size and UTF-8 checks apply to the downloaded bytes too); `FetchClientError` and `PageError`
→ `ToolError(str(exc))`.

`__main__` (stdio) and `build_http_app` build `FetchClient(config.fetch, config.pages.max_bytes)`
when `config.fetch` is set and pass it as `AppContext.fetcher`. The limits are therefore the same
on every path: `pages.max_bytes` (16 MB by default) for the page, `fetch.timeout_seconds` per
network operation.

### Rejected alternatives (the ADRs' "Alternatives considered")

- **A PrivacyFence connector** (the original plan): makes a gateway into a publisher with its own
  SSH key and page index; six phases of PrivacyFence surface for something that governs no
  existing system.
- **SFTP to a remote web server** (the original transport): needs an SSH key pair, host-key
  pinning and asyncssh (EPL-2.0/GPL-2.0+). Running on the web server removes all of it. The
  `PageStore` protocol keeps the door open.
- **Serving pages from this process**: would put untrusted page scripts on the OAuth server's
  origin, and put page traffic through Python. Caddy serves static files better.
- **Adopting an existing project** (artifact-mcp, The Artifact, mcpStaticHosting): all young;
  The Artifact is AGPL with SSO in a separately licensed folder; the others lack claude.ai's OAuth
  or are multi-file site tools.
- **Letting Caddy or an identity-aware proxy handle sign-in**: claude.ai needs the MCP server's
  own OAuth metadata, dynamic registration and PKCE, which a proxy does not provide.
- **Any OIDC provider**: the user chose Google Workspace; `hd` is Google-specific. A second
  provider is a new `*_oidc.py` beside this one.
- **A consent page after sign-in**: replaced by the redirect URI allowlist, which removes the
  attacker-registered-client case outright instead of asking the person to spot it. The browser
  binding cookie (D10) covers the remaining case, a Google sign-in link forwarded to someone else.
- **Verifying the ID token signature with Google's JWKS** (PyJWT's `PyJWKClient`, installed with
  `mcp`): adds a key fetch, a key cache and a key-rotation failure mode for no gain, since OpenID
  Connect Core section 3.1.3.7 makes TLS validation of the token endpoint sufficient for a token
  received directly from it.
- **Refresh tokens that renew their own lifetime**: someone removed from the allowlist would keep
  access by refreshing forever; the lifetime is absolute and the allowlist is re-checked on every
  refresh and every request.
- **JWT access tokens**: cannot be revoked without a deny list; opaque tokens in SQLite can.
- **JSON files for state**: several concurrent writers (sign-ins, publishes, uploads) need
  transactions; SQLite is in the standard library.
- **Fetching any URL** for `publish_page_from_url`: a model-supplied address could point at
  cloud metadata endpoints or internal services. An administrator-written host list, https only,
  redirects re-checked against it, and resolved addresses required to be public.
- **Fetching through `httpx2`**: it comes with `mcp` but is not a declared dependency and its
  name may change with the SDK; `urllib` is standard library and its redirect handler is the one
  hook needed.
- **Fetching claude.ai artifacts by link**: an artifact's link serves a viewer page and is private
  by default, so there is no raw file to fetch.
- **Stateful Streamable HTTP sessions**: lost on restart, and a long-lived stream behind a reverse
  proxy for no benefit; the tools need no server-to-client messages.

## ADRs

- **0011** — HTML Artifact Deploy is a standalone MCP server that runs on the web server and
  writes pages into the folder the web server serves, behind a `PageStore` interface; it never
  serves pages itself. (Rejected: PrivacyFence connector, SFTP, serving from the process, adopting
  an existing project.)
- **0012** — Pages are served from a separate host name with a `Content-Security-Policy: sandbox`
  header; the configuration refuses a pages host equal to the server's host.
- **0013** — A page's link is a random 120-bit id plus a readable slug, open to anyone with the
  link; ownership lives in a per-person index, and the folder is never listed.
- **0014** — The server is its own OAuth 2.1 authorization server (dynamic registration, opaque
  tokens hashed in SQLite) and delegates only identity to Google Workspace.
- **0015** — Dynamic client registration accepts only allowlisted redirect URIs (claude.ai,
  claude.com, loopback) instead of showing a consent page, and each Google sign-in is bound to the
  browser that started it by a cookie.
- **0016** — Google's ID token is trusted because it comes straight from Google's token endpoint
  over TLS, not by checking its signature against Google's published keys.
- **0017** — All server state is one SQLite file with `PRAGMA user_version` as its schema version.
- **0018** — Large pages arrive through one-time capability upload URLs, not a tool argument.
- **0019** — Streamable HTTP runs stateless with JSON responses, and `starlette` and `uvicorn`
  are declared runtime dependencies (already installed by `mcp`).
- **0020** — The server downloads a page by URL only from administrator-listed https hosts,
  directly (never through a proxy), re-checking every redirect and refusing non-public addresses;
  off unless `[fetch]` is configured. Accepted: the DNS re-resolution window, and that a
  proxy-only network cannot use it.

## Manual steps

Nothing before implementation: every phase builds and tests against fakes (a stubbed Google
client, a temporary pages folder, an in-process HTTP client), and the Google discovery fixture is
public and recorded by a phase.

After implementation (`manual_after`), on real infrastructure, following the step-by-step page
(link in the manifest):
- **ma1** — Create the Google OAuth client, set up an Ubuntu 24.04 server with Caddy and two DNS
  names, deploy the feature branch, and check the pages host's headers.
- **ma2** — claude.ai (and Claude Desktop through the same connector): sign in, publish, replace,
  list, take down, publish from an allowed URL and be refused for another host; a second,
  non-allowed Google account is refused.
- **ma3** — Claude Code: OAuth sign-in, an API token, and a large page through
  `create_page_upload`.

## Risks and open questions

- **100% coverage per phase.** `OVERALL_FLOOR = 100.0`. A phase that leaves any line or branch of
  `src/` uncovered fails `tests.yml`. Workers run
  `python3 -m pytest --cov=src/html_artifact_deploy --cov-branch --cov-report=json:coverage.json -q && python3 scripts/check_coverage_floor.py coverage.json`
  before finishing. The only allowed `# pragma: no cover` is `_run_uvicorn` (D11) and the
  existing `if __name__ == "__main__"` guard. If a worker cannot cover a branch without a pragma,
  it stops with `status=blocked` naming the line.
- **SDK surface.** The design is written against `mcp 2.2.0`. If `OAuthAuthorizationServerProvider`'s
  method signatures, `AuthorizationCode.subject`, `custom_route`, or
  `streamable_http_app(..., stateless_http=, json_response=, transport_security=, host=)` differ
  from `.venv/lib/python3.11/site-packages/mcp/server/…` as the worker finds them, the worker stops
  with `status=blocked` and quotes the real signature.
- **DNS-rebinding protection** (D11). Anything that would need
  `enable_dns_rebinding_protection=False` in `build_http_app` is a stop (`status=blocked`); tests
  that build their own bare app (p5) may disable it.
- **claude.ai callback URL.** D2's default allowlist assumes claude.ai's MCP OAuth callback is
  `https://claude.ai/api/mcp/auth_callback` (and the claude.com equivalent). If ma2 shows a
  different redirect URI in the registration error, the fix is one default in `config.py` and a
  note in the PR; the allowlist is configurable meanwhile.
- **Project-history guard.** `tests/unit/test_no_project_history.py` scans `docs/`, `deploy/*.toml`
  and code: no `Phase 3`, `P2`, `#12` or `§3` in any file a phase writes except this plan. The
  OIDC spec section is cited as "section 3.1.3.7", never with `§`.
- **Stdio needs a config now.** `python -m html_artifact_deploy` with no config file exits 2. The
  stdio integration test and the packaged test must write a temporary config and pass
  `--config`; the packaged test runs inside `build.yml`, so p3 must dispatch `build.yml` against its
  phase branch (steward table) and report the run.
- **urllib redirect behaviour** (p8). The design relies on `HTTPRedirectHandler.redirect_request`
  being called for every 301/302/303/307/308 hop, with `len(req.redirect_dict)` equal to the
  hops so far; a p8 test asserts the counts 0, 1, 2 for three hops on the installed Python. If the installed Python's `urllib.request` differs, the worker stops with
  `status=blocked` and quotes it. No live check is added for fetching: there is no fixed upstream.
- **Google fixture recording** needs outbound HTTPS to `accounts.google.com`, which this container
  has. If a worker's container does not, it runs `/qa-record google_openid_configuration`
  (dispatches `qa-record-fixture.yml`); if that queues for more than 30 minutes (no runner online),
  it stops with `status=blocked`.

## Implementation manifest

```yaml
plan_slug: publish-pages
feature_branch: feature/publish-pages
max_parallel: 2
manual_steps_artifact: https://claude.ai/artifact/9onBRNhjomox4Dr1Hyaa3U
manual_steps_source: docs/publish-pages-plan-manual-steps.html
manual_before: []
manual_after:
  - id: ma1-server-setup
    title: Create the Google OAuth client and deploy the feature branch on an Ubuntu 24.04 server with Caddy
    why: Real DNS, TLS, Google Workspace sign-in, systemd and Caddy headers cannot run in CI
  - id: ma2-claude-ai
    title: From claude.ai (and Claude Desktop), sign in, publish, replace, list and take down a page, publish from an allowed URL and be refused for another host; a non-allowed account is refused
    why: claude.ai's OAuth client (dynamic registration, callback URL) is only testable against a real public server
  - id: ma3-claude-code
    title: From Claude Code, sign in with OAuth, then with an API token, and publish a large page through create_page_upload
    why: The loopback OAuth callback and the upload URL need a real client and server
verify_after_merge:
  - python3 -m pip install --quiet -e ".[dev]"
  - python3 -m pytest -q --cov=src/html_artifact_deploy --cov-branch --cov-report=json:coverage.json
  - python3 scripts/check_coverage_floor.py coverage.json
  - ruff format --check .
  - python3 scripts/mypy_strict_modules.py
  - bandit -q -c pyproject.toml -r src
final_checks:
  - docs/publish-pages-plan.md and docs/publish-pages-plan-manual-steps.html are deleted and nothing links to them (grep -rn "publish-pages-plan" . --exclude-dir=.git returns nothing)
  - ADRs 0011 to 0020 exist, each is Accepted, and each is in docs/adr/README.md's index
  - CHANGELOG.md has [Unreleased] entries for the tools, sign-in, uploads, fetching by URL and deployment, and no version heading
  - echo appears nowhere in src/ or tests/ (grep -rn '"echo"' src tests returns nothing)
  - build.yml dispatched against feature/publish-pages is green, and the run is linked in the PR
phases:
  - id: p1-config-and-storage
    title: Configuration file, ids and slugs, local-folder page store
    depends_on: []
    complexity: M
    touches:
      - src/html_artifact_deploy/config.py
      - src/html_artifact_deploy/pages.py
      - src/html_artifact_deploy/storage.py
      - tests/unit/test_config.py
      - tests/unit/test_pages.py
      - tests/unit/test_storage.py
      - deploy/config.example.toml
    brief: |
      Read docs/publish-pages-plan.md sections D1, D2, D3, D4 first; they are the spec. Do not edit CHANGELOG.md or
      pyproject.toml (p3 and p10 own them).
      1. Create src/html_artifact_deploy/pages.py exactly as D3 (module docstring, `from __future__ import annotations`).
      2. Create src/html_artifact_deploy/config.py exactly as D2: the constants (DEFAULT_SECRET_ENV with its nosec
         comment), the four dataclasses, ConfigError, resolve_config_path, load_config (every check in the D2 table, in
         that order, with those messages prefixed by f"{path}: "), require_http, is_allowed_email,
         google_client_secret. Use tomllib. load_config never reads the secret.
      3. Create src/html_artifact_deploy/storage.py exactly as D4 (StorageError, PageStore Protocol, LocalFolderStore).
      4. Create deploy/config.example.toml: the D2 example, each key with a one-line comment saying what it is and
         whether it is required.
      5. Tests (marker `unit`, class-per-behaviour, docstring naming the module):
         - tests/unit/test_pages.py: new_page_id matches PAGE_ID_RE and 1000 calls are distinct; slugify on
           "Q3 report: Ünits & Co." == "q3-report-units-co", on "" and "!!!" == "page", on "a" * 100 has length 60,
           on "a" * 59 + " b" equals "a" * 59; clean_title rejects "", "   ", 201 chars and strips; page_url format
           and its ValueError for a bad id or slug.
         - tests/unit/test_config.py: a full file (TOML written into tmp_path) loads into the expected Config; a minimal
           file (only [pages] and [state]) gives http=None, auth=None, stdio_user="local", max_bytes=16_000_000; one
           parametrized case per row of the D2 table asserting the exact message; resolve_config_path precedence
           (cli > env > default); require_http error; is_allowed_email (exact address in any case; hd in
           allowed_domains; hd None; hd of another domain; an address whose own domain is allowed but hd is None →
           False); google_client_secret returns the value, and raises the D2 message when unset and when empty;
           deploy/config.example.toml loads with an empty environ.
         - tests/unit/test_storage.py: write then read back (bytes, file `<root>/<page_id>/<slug>.html`), replace keeps
           one file and no `.tmp` leftovers, modes 0o755 and 0o644, delete returns True then False, ValueError for a
           bad id or slug in write and delete, a symlinked page folder is refused in write and delete, check() on a
           missing root and on a read-only root (chmod 0o555; skip if os.geteuid() == 0), an OSError from os.replace
           removes the temp file and raises StorageError with D4's write message, the same with os.unlink also raising
           (the error still propagates as StorageError), an OSError from tempfile.mkstemp, and an OSError from
           shutil.rmtree raising StorageError with D4's delete message.
      6. Run the coverage command from the plan's Risks section; the whole suite must stay at 100%.
      Stop with status=blocked if tomllib rejects the D2 example as written, or if 100% branch coverage of config.py
      needs a `# pragma: no cover`.
    acceptance:
      - python3 -m pytest tests/unit/test_config.py tests/unit/test_pages.py tests/unit/test_storage.py -q passes
      - python3 -m pytest -q --cov=src/html_artifact_deploy --cov-branch --cov-report=json:coverage.json && python3 scripts/check_coverage_floor.py coverage.json passes
      - mypy --strict src/html_artifact_deploy/config.py src/html_artifact_deploy/pages.py src/html_artifact_deploy/storage.py passes
      - ruff check . && ruff format --check . && bandit -q -c pyproject.toml -r src pass

  - id: p2-state-db
    title: SQLite state database and the per-person page index
    depends_on: []
    complexity: M
    touches:
      - src/html_artifact_deploy/state.py
      - src/html_artifact_deploy/page_index.py
      - tests/unit/test_state.py
      - tests/unit/test_page_index.py
    brief: |
      Read docs/publish-pages-plan.md sections D1, D5, D6 first; they are the spec. Do not edit CHANGELOG.md or
      pyproject.toml.
      1. Create src/html_artifact_deploy/state.py exactly as D5: SCHEMA_VERSION, DB_FILE_NAME, StateError, secret_hash,
         StateDB with __init__, transaction(), close(). The whole schema-version-1 SQL from D5 (all six tables and three
         indexes, including the binding_hash, hosted_domain, family and used_at columns) runs in one transaction when
         user_version is 0.
      2. Create src/html_artifact_deploy/page_index.py exactly as D6 (PageRecord, PageIndex). Each method uses
         db.transaction(). `get` filters on both owner and page_id. `list` orders by updated_at DESC, page_id ASC,
         LIMIT limit. `upsert` is INSERT … ON CONFLICT(page_id) DO UPDATE SET owner, title, slug, bytes, sha256,
         updated_at (created_at is not overwritten).
      3. Tests (marker `unit`), database under tmp_path:
         - tests/unit/test_state.py: a new file gets user_version 1, all six tables and their columns (compare
           `PRAGMA table_info` names with D5); reopening does not re-run the schema; a file with user_version 7 raises
           StateError with the D5 message; the file mode is 0o600; transaction() rolls back on an exception (insert,
           raise, row absent); close() then a transaction raises sqlite3.ProgrammingError; secret_hash equals
           hashlib.sha256 hex.
         - tests/unit/test_page_index.py: upsert/get round trip; get with another owner returns None; list ordering,
           limit, other owners' pages excluded; upsert of an existing page keeps created_at; remove returns True then
           False; remove with another owner returns False and leaves the row.
      4. Keep the whole suite at 100% coverage (Risks section command).
    acceptance:
      - python3 -m pytest tests/unit/test_state.py tests/unit/test_page_index.py -q passes
      - python3 -m pytest -q --cov=src/html_artifact_deploy --cov-branch --cov-report=json:coverage.json && python3 scripts/check_coverage_floor.py coverage.json passes
      - mypy --strict src/html_artifact_deploy/state.py src/html_artifact_deploy/page_index.py passes
      - ruff check . && ruff format --check . && bandit -q -c pyproject.toml -r src pass

  - id: p3-service-and-stdio-tools
    title: PageService, the publish/list/unpublish tools, stdio with --config, first mypy promotions
    depends_on: [p1-config-and-storage, p2-state-db]
    complexity: M
    touches:
      - src/html_artifact_deploy/service.py
      - src/html_artifact_deploy/server.py
      - src/html_artifact_deploy/__main__.py
      - pyproject.toml
      - scripts/check_coverage_floor.py
      - tests/unit/test_service.py
      - tests/unit/test_server.py
      - tests/unit/test_main.py
      - tests/integration/test_stdio_contract.py
      - tests/packaged/test_installed_server.py
      - README.md
    brief: |
      Read docs/publish-pages-plan.md sections D1, D6, D7, D8, D13 first; they are the spec. Do not edit CHANGELOG.md.
      1. Create src/html_artifact_deploy/service.py exactly as D7 (PageError, PublishResult, PageService with url_for,
         publish, check_can_publish, list_pages, unpublish). Index calls go through asyncio.to_thread.
      2. Rewrite src/html_artifact_deploy/server.py per D8: keep SERVER_NAME and server_info; delete echo; add AppContext
         with only `service` and `owner` (uploads.py does not exist yet; p7 adds the `uploads` field) and
         build_server(app, *, auth_server_provider=None, auth=None), passing both to MCPServer. Register publish_page,
         list_pages, unpublish_page with D8's exact docstrings and ToolAnnotations. In this phase a non-empty upload_id
         (with html empty) always raises ToolError("Uploads are not available on this connection; pass the page as html.").
         Update the module docstring: it describes the page tools and that the caller's identity comes from AppContext.
      3. Rewrite src/html_artifact_deploy/__main__.py for the stdio part of D13 only: `main(argv=None, environ=None)`,
         top-level `--config` and `--version`, no subcommands yet. Load config (resolve_config_path + load_config), build
         StateDB(config.state_dir / DB_FILE_NAME), LocalFolderStore(config.pages.root) with check(), PageIndex,
         PageService, AppContext(owner=lambda: config.stdio_user), then build_server(...).run("stdio").
         ConfigError/StorageError/StateError → print f"html-artifact-deploy: {exc}" to sys.stderr, return 2.
      4. pyproject.toml: turn the mypy override's `module = "html_artifact_deploy.server"` into
         `module = ["html_artifact_deploy.server", "html_artifact_deploy.config", "html_artifact_deploy.pages",
         "html_artifact_deploy.storage", "html_artifact_deploy.state", "html_artifact_deploy.page_index",
         "html_artifact_deploy.service"]`. scripts/check_coverage_floor.py: add `config.py` and `storage.py` to
         MODULE_FLOORS at 100.0, each with a one-line comment (what it guards: the configuration's fail-closed checks;
         writes into the published folder).
      5. Tests:
         - tests/unit/test_service.py: every step of D7's publish order with its exact message; a replacement keeps slug
           and created_at and sets created=False; another owner's page_id is refused; StorageError mapping for publish
           and unpublish (a fake PageStore raising StorageError; the index is unchanged after a failed unpublish);
           unpublish of a page whose folder is already gone succeeds; list clamping (0 → 1, 500 → 200);
           check_can_publish; the log line has page_id and owner and not the title (caplog).
         - tests/unit/test_server.py: rewrite. A fixture builds a real PageService on tmp_path (LocalFolderStore +
           StateDB) and AppContext(owner=lambda: "alice@example.com"). Through `mcp.Client(build_server(...))`: tool list
           == {"publish_page", "list_pages", "unpublish_page", "server_info"}; each tool's annotations equal D8's; every
           tool has a description; publish → file on disk and url shape; replace; list; unpublish → file gone; each
           ToolError path returns is_error with the exact message (both html and upload_id; neither; upload_id alone;
           unknown page_id; title too long); a second owner cannot see or remove alice's page (a second server with owner
           "bob@example.com" over the same database); server_info unchanged; TestFreshInstances kept.
         - tests/unit/test_main.py: REPLACE `test_runs_the_server_over_stdio` (it calls main([]) with no config, which
           now returns 2). New tests: `--version` still works; a missing config file returns 2 with the D2 message on
           stderr and nothing on stdout (capsys); a config whose pages.root does not exist returns 2; a state.dir file
           with user_version 7 returns 2; with a valid config, monkeypatch MCPServer.run to record its argument and assert
           "stdio"; `environ` defaults to os.environ (set HTML_ARTIFACT_DEPLOY_CONFIG with monkeypatch.setenv).
         - tests/integration/test_stdio_contract.py: write a config into tmp_path (pages.root and state.dir under tmp_path,
           public_base_url "https://pages.example.com"); pass ["-m", "html_artifact_deploy", "--config", path]; replace the
           echo test by a publish_page call whose url starts with "https://pages.example.com/" and whose file exists
           under tmp_path; keep the unknown-tool test.
         - tests/packaged/test_installed_server.py: the same temporary config and --config for the stdio test; still calls
           server_info.
      6. README.md: replace the tool table rows with publish_page, list_pages, unpublish_page, server_info (one line each);
         change the Claude Code and Claude Desktop examples to pass `--config ~/html-artifact-deploy.toml`. Leave the rest
         for p9.
      7. Full suite at 100% coverage. Then dispatch build.yml against this phase branch (steward table: "The built wheel,
         and its packaged smoke test") and put the run URL and result in the PHASE-REPORT. A red packaged job is this
         phase's to fix.
      Stop with status=blocked if ToolAnnotations cannot be passed to @server.tool, or if list_tools() does not return them.
    acceptance:
      - python3 -m pytest tests/unit tests/integration -q passes
      - python3 -m pytest -q --cov=src/html_artifact_deploy --cov-branch --cov-report=json:coverage.json && python3 scripts/check_coverage_floor.py coverage.json passes
      - python3 scripts/mypy_strict_modules.py --list | grep -c "src/html_artifact_deploy/" prints 7
      - grep -rn '"echo"\|def echo' src tests returns nothing
      - python3 -m html_artifact_deploy --config /nonexistent.toml exits 2 and prints to stderr only
      - build.yml dispatched against the phase branch is green (run URL in PHASE-REPORT)
      - ruff check . && ruff format --check . && python3 scripts/mypy_strict_modules.py && bandit -q -c pyproject.toml -r src pass

  - id: p4-google-client
    title: Google OpenID Connect client and its live discovery check
    depends_on: []
    complexity: S
    touches:
      - src/html_artifact_deploy/google_oidc_client.py
      - tests/unit/test_google_oidc_client.py
      - scripts/live_check.py
      - tests/fixtures/live/google_openid_configuration/openid-configuration.json
    brief: |
      Read docs/publish-pages-plan.md section D9 first; it is the spec. Do not edit CHANGELOG.md or pyproject.toml.
      1. Create src/html_artifact_deploy/google_oidc_client.py exactly as D9 (constants with GOOGLE_TOKEN_ENDPOINT's nosec
         comment, GoogleOidcClientError, GoogleIdentity, GoogleOidcClient with authorization_url, exchange_code,
         _post_token_request, and a private `_decode_id_token(id_token: str) -> dict[str, Any]`). The module docstring
         states in one or two sentences why the signature is not verified (OpenID Connect Core 1.0 section 3.1.3.7;
         write "section", never the section sign).
      2. scripts/live_check.py: add the google_openid_configuration LiveCheck from D9 (fetch and redact functions at
         module level, above CHECKS), and replace the comment `# name -> check. Empty until the server talks to an
         upstream service.` with `# name -> check.`.
      3. Record the fixture: `python3 scripts/live_check.py --record google_openid_configuration`. Read the diff: only the
         six keys from D9. Then `python3 scripts/live_check.py --check google_openid_configuration` must print `ok`. If
         the container has no route to accounts.google.com, run `/qa-record google_openid_configuration` instead (Risks).
      4. tests/unit/test_google_oidc_client.py (marker `unit`):
         - authorization_url contains every D9 parameter (parse with urllib.parse and compare the dict).
         - exchange_code with `_post_token_request` monkeypatched to return a dict whose id_token is built in the test
           (header.payload.signature, base64url without padding): success returns the lowercased email, hd and sub; the
           form sent has grant_type, code, redirect_uri, client_id, client_secret.
         - one parametrized failure case per D9 check (wrong iss, wrong aud, expired exp with an injected clock, nonce
           mismatch, missing email, email_verified False or missing, an id_token of 2 parts, bad base64, a non-JSON
           payload, no id_token key), each raising GoogleOidcClientError("Google sign-in failed.") and logging "Google
           sign-in failed" without the code or token (caplog).
         - _post_token_request: monkeypatch urllib.request.urlopen with a fake returning JSON bytes (assert the Request's
           URL, method, and timeout=10), with one raising urllib.error.URLError, and with one returning non-JSON bytes;
           the last two raise GoogleOidcClientError.
         - class TestLiveFixtureParsing: load the committed fixture; authorization_endpoint ==
           GOOGLE_AUTHORIZATION_ENDPOINT, token_endpoint == GOOGLE_TOKEN_ENDPOINT, issuer in GOOGLE_ISSUERS, "email" in
           claims_supported.
      5. Full suite at 100% coverage.
    acceptance:
      - python3 -m pytest tests/unit/test_google_oidc_client.py -q passes
      - python3 scripts/live_check.py --check google_openid_configuration exits 0
      - python3 -m pytest -q --cov=src/html_artifact_deploy --cov-branch --cov-report=json:coverage.json && python3 scripts/check_coverage_floor.py coverage.json passes
      - ruff check . && ruff format --check . && bandit -q -c pyproject.toml -r src pass
      - mypy --strict src/html_artifact_deploy/google_oidc_client.py passes

  - id: p5-oauth-provider
    title: Token store and the OAuth 2.1 authorization server backed by Google sign-in
    depends_on: [p1-config-and-storage, p2-state-db, p4-google-client]
    complexity: M
    worker_model: opus
    worker_model_reason: This is the server's authentication boundary (dynamic registration, browser binding, code and refresh-token replay, the allowlist re-check); a subtle mistake is a security hole that tests written by the same worker may not catch.
    touches:
      - src/html_artifact_deploy/token_store.py
      - src/html_artifact_deploy/oauth_provider.py
      - tests/unit/test_token_store.py
      - tests/unit/test_oauth_provider.py
    brief: |
      Read docs/publish-pages-plan.md sections D2 (AuthConfig, is_allowed_email), D5, D9, D10 first; they are the spec.
      Read .venv/lib/python3.11/site-packages/mcp/server/auth/provider.py, settings.py, routes.py and handlers/ before
      writing: implement the provider Protocol with the SDK's exact method signatures. Do not edit CHANGELOG.md or
      pyproject.toml.
      1. Create src/html_artifact_deploy/token_store.py exactly as D10's TokenStore list (prefix constants with their
         nosec comments).
      2. Create src/html_artifact_deploy/oauth_provider.py exactly as D10: constants, is_allowed_redirect_uri,
         GoogleOAuthProvider with every Protocol method, handle_google_start, handle_google_callback, the `page()` helper,
         and auth_settings(http).
      3. tests/unit/test_token_store.py: every method; expiry with an injected clock; mark_code_used and mark_used return
         True once then False; get_code reports used_family after mark_code_used; bind_auth_request binds once;
         revoke_family; duplicate API token name → ValueError with D10's message; create_api_token lowercases the
         subject; purge_expired (including used rows and an orphaned 31-day-old client, but not a client a token names);
         no plaintext token or code appears in the database file (read its bytes and search for the string).
      4. tests/unit/test_oauth_provider.py, with a fake GoogleOidcClient (a small class with authorization_url and an async
         exchange_code returning a configured GoogleIdentity or raising GoogleOidcClientError). Build the app as
         `MCPServer("t", auth_server_provider=provider, auth=auth_settings(http))` with one trivial tool, register
         START_PATH and CALLBACK_PATH custom routes, then `streamable_http_app(streamable_http_path="/mcp",
         json_response=True, stateless_http=True, transport_security=TransportSecuritySettings(enable_dns_rebinding_protection=False))`,
         and drive it with httpx2.AsyncClient(transport=httpx2.ASGITransport(app=app), base_url=http.public_url) inside
         `async with app.router.lifespan_context(app):`. Use http.public_url = "http://127.0.0.1:8765" (so the cookie is
         "hda_signin" without Secure). Flow tests use the real clock; expiry tests call the provider directly.
         - is_allowed_redirect_uri: both claude defaults, http://localhost:33418/callback, http://127.0.0.1/x,
           http://[::1]:5/cb allowed; https://evil.example/cb, http://example.com/cb, https://claude.ai/other refused.
         - register_client refuses a client with one bad redirect URI among good ones (code and message).
         - Full flow: POST /register → GET /authorize (PKCE S256, redirect_uri http://localhost:9999/cb) → 302 to
           START_PATH → GET START_PATH → 302 to the Google URL (state and nonce present) with the binding cookie → GET
           CALLBACK_PATH?state=…&code=… (httpx2 client keeps the cookie) → 302 to the redirect URI with a code and the
           client's state → POST /token → access and refresh tokens → POST /mcp tools/list with the bearer token succeeds
           and without it gets 401 → refresh once works → replaying the old refresh token fails invalid_grant AND the new
           refresh and access tokens stop working → replaying the authorization code fails invalid_grant AND revokes the
           tokens issued from it → POST /revoke with an access token makes it and its refresh token fail.
         - Negatives: authorize with a resource other than "<public_url>/mcp" (invalid_target); authorize when
           MAX_PENDING_SIGN_INS requests are pending (temporarily_unavailable; monkeypatch the constant to 2); START_PATH
           with an unknown state (400) and a second time for the same state (400); callback with an unknown state (400),
           reused (400), with no cookie (400, "different browser" message), with a wrong cookie (400);
           `error=access_denied` (302 with error=access_denied and the client's state); GoogleOidcClientError (502); a
           non-allowed email (403, the message has the html-escaped address); hosted_domain None with only
           allowed_domains configured (403); every callback response has Cache-Control: no-store.
         - Allowlist re-check: after a successful flow, build a second provider over the same store with an AuthConfig that
           no longer allows the address: load_access_token returns None, the refresh fails, and the family is gone.
         - Direct calls: an expired code is not loadable (injected clock); an access token past ACCESS_TOKEN_TTL returns
           None; the rotated refresh token keeps the original expires_at; an API token loads as an AccessToken with
           client_id "api-token" and scopes ["pages"], and revoke_token does not remove it; the race branches
           (mark_code_used or mark_used already done before exchange_* is called) raise invalid_grant.
      5. Full suite at 100% coverage.
      Stop with status=blocked if the SDK's /authorize handler does not validate redirect_uri against the client's
      registered list before calling provider.authorize, if the SDK follows the authorize() return value anywhere other
      than as a 302 Location, or if AuthorizationCode has no `subject` field.
    acceptance:
      - python3 -m pytest tests/unit/test_token_store.py tests/unit/test_oauth_provider.py -q passes
      - python3 -m pytest -q --cov=src/html_artifact_deploy --cov-branch --cov-report=json:coverage.json && python3 scripts/check_coverage_floor.py coverage.json passes
      - mypy --strict src/html_artifact_deploy/token_store.py src/html_artifact_deploy/oauth_provider.py passes
      - ruff check . && ruff format --check . && bandit -q -c pyproject.toml -r src pass

  - id: p6-http-transport
    title: Streamable HTTP app, serve-http, check-config and token commands
    depends_on: [p3-service-and-stdio-tools, p5-oauth-provider]
    complexity: M
    touches:
      - src/html_artifact_deploy/http_app.py
      - src/html_artifact_deploy/__main__.py
      - pyproject.toml
      - scripts/check_coverage_floor.py
      - tests/unit/test_http_app.py
      - tests/unit/test_main.py
      - tests/integration/test_http_contract.py
    brief: |
      Read docs/publish-pages-plan.md sections D2 (google_client_secret), D10 (auth_settings, START_PATH), D11, D13 first;
      they are the spec. Do not edit CHANGELOG.md.
      1. pyproject.toml dependencies: add `"starlette>=1.0,<2.0"` and `"uvicorn>=0.30,<1.0"` below mcp, with the comment
         `# Imported directly by http_app.py and __main__.py; mcp already installs both (ADR 0019).` First confirm the
         installed versions fall in those ranges (`pip show starlette uvicorn`); if not, stop blocked. Add
         "html_artifact_deploy.google_oidc_client", "html_artifact_deploy.token_store", "html_artifact_deploy.oauth_provider",
         "html_artifact_deploy.http_app" and "html_artifact_deploy.__main__" to the mypy override's module list.
         scripts/check_coverage_floor.py: add token_store.py, oauth_provider.py and http_app.py at 100.0 with one-line
         comments.
      2. Create src/html_artifact_deploy/http_app.py exactly as D11, without uploads (AppContext has no uploads field yet;
         p7 adds it). Register the START_PATH, CALLBACK_PATH and HEALTH_PATH custom routes before streamable_http_app.
      3. __main__.py: add the subcommands of D13 with argparse subparsers (`dest="command"`, not required, so no subcommand
         = stdio): serve-http (google_client_secret, build_http_app, then _run_uvicorn), check-config (load_config, the
         store's check(), StateDB open, google_client_secret when [auth] is present), token create/list/revoke.
         `_run_uvicorn` exactly as D11 with `# pragma: no cover`.
      4. tests/unit/test_http_app.py, via httpx2.ASGITransport with base_url=http.public_url (Host is checked, D11) inside
         `async with app.router.lifespan_context(app):`: GET /healthz → 200 "ok"; GET /.well-known/oauth-authorization-server
         → issuer equals http.public_url and registration_endpoint present; GET /.well-known/oauth-protected-resource/mcp
         → resource equals public_url + "/mcp"; POST /mcp without a token → 401; with an API token from
         TokenStore.create_api_token → tools/list returns the three page tools and server_info, and publish_page writes
         a file whose index owner is the token's subject; with a valid token and header Host: evil.example → 421;
         max_request_body_size arithmetic (small and large max_bytes); build_http_app raises ConfigError when [http] is
         missing and when the secret variable is empty.
      5. tests/unit/test_main.py: check-config prints "Configuration OK: <path>" and returns 0, returns 2 with a bad file,
         and returns 2 when [auth] is present and the secret is unset; token create prints only the token on stdout and
         the D13 notice on stderr, and works without the secret variable; a refused address → 2; a duplicate name → 2
         with the D10 message; token list's line format; token revoke → "Revoked." (0) and "no such token." (1);
         serve-http with _run_uvicorn monkeypatched records the app and the HttpConfig; serve-http without [http] → 2;
         serve-http without the secret → 2.
      6. tests/integration/test_http_contract.py (marker `integration`, `@pytest.mark.timeout(60)`): write a config
         (listen_port = a free port from binding a socket to port 0; public_url f"http://127.0.0.1:{port}"; pages
         public_base_url "https://pages.example.com"; auth with allowed_domains ["example.com"] and client id
         "x.apps.googleusercontent.com"); create an API token with `python -m html_artifact_deploy --config … token create
         --user a@example.com --name t` (subprocess, no secret needed); start `python -m html_artifact_deploy --config …
         serve-http` as a subprocess with HTML_ARTIFACT_DEPLOY_GOOGLE_CLIENT_SECRET=x in its env; poll /healthz every
         0.2 s until 200 (at most 20 s); then `mcp.Client` over streamable_http_client(f"http://127.0.0.1:{port}/mcp",
         http_client=httpx2.AsyncClient(headers={"Authorization": f"Bearer {token}"})) lists tools and publishes a page
         whose file exists; terminate the subprocess in a finally and assert nothing it printed contains the token.
      7. Full suite at 100% coverage. Dispatch build.yml against this phase branch (a runtime dependency changed; definition
         of done) and put the run URL and result in the PHASE-REPORT.
      Stop with status=blocked if streamable_http_app does not mount the OAuth routes when auth_server_provider is set,
      or if stateless_http=True rejects authenticated requests.
    acceptance:
      - python3 -m pytest tests/unit/test_http_app.py tests/unit/test_main.py tests/integration -q passes
      - python3 -m pytest -q --cov=src/html_artifact_deploy --cov-branch --cov-report=json:coverage.json && python3 scripts/check_coverage_floor.py coverage.json passes
      - python3 scripts/mypy_strict_modules.py --list | grep -c "src/html_artifact_deploy/" prints 12
      - grep -c "pragma: no cover" src/html_artifact_deploy/__main__.py prints 2
      - build.yml dispatched against the phase branch is green (run URL in PHASE-REPORT)
      - ruff check . && ruff format --check . && python3 scripts/mypy_strict_modules.py && bandit -q -c pyproject.toml -r src pass

  - id: p7-uploads
    title: One-time upload URLs and the create_page_upload tool
    depends_on: [p6-http-transport]
    complexity: M
    touches:
      - src/html_artifact_deploy/uploads.py
      - src/html_artifact_deploy/server.py
      - src/html_artifact_deploy/http_app.py
      - pyproject.toml
      - scripts/check_coverage_floor.py
      - tests/unit/test_uploads.py
      - tests/unit/test_server.py
      - tests/unit/test_http_app.py
    brief: |
      Read docs/publish-pages-plan.md sections D7 (check_can_publish), D8 (publish_page upload flow, create_page_upload),
      D11, D12 first; they are the spec. Do not edit CHANGELOG.md.
      1. Create src/html_artifact_deploy/uploads.py exactly as D12. Database and file work goes through asyncio.to_thread;
         handle_put reads with `async for chunk in request.stream()` and writes each chunk through to_thread.
      2. server.py: add `uploads: UploadStore | None = None` to AppContext; implement the upload branch of publish_page
         exactly as D8 (check_can_publish → read → publish → discard; UploadError → ToolError); register
         create_page_upload with D8's docstring and annotations only when app.uploads is not None. Its result: upload_id,
         upload_url, method "PUT", max_bytes, expires_at (ISO).
      3. http_app.py: build the UploadStore as D12 says, pass it in AppContext, and register the PUT custom route before
         streamable_http_app is called.
      4. pyproject.toml: add "html_artifact_deploy.uploads" to the mypy override's module list. scripts/check_coverage_floor.py:
         add uploads.py at 100.0 with a one-line comment.
      5. Tests (all HTTP through build_http_app's app, httpx2.ASGITransport, base_url=http.public_url, inside the lifespan
         context, with a stub GoogleOidcClient passed as `google=`):
         - tests/unit/test_uploads.py: create → PUT → read → discard; unknown id 404; a second PUT to a filled slot 404;
           an expired slot (injected clock) 404 and not readable; Content-Length over the limit 413; a streamed body over
           the limit without Content-Length (an async generator body) 413 and no file left in the folder; empty body 400;
           read by another owner → UploadError with D12's message; the eleventh open slot → UploadError with D12's
           message; the upload folder is 0o700; expired rows, their files and stale `.part` files are purged on create;
           the losing side of a concurrent fill (mark the row filled directly before handle_put's UPDATE runs, via
           monkeypatch) gets 404 and leaves no file.
         - tests/unit/test_server.py: with an UploadStore in AppContext the tool list includes create_page_upload with D8's
           annotations; publish via upload_id works and discards the upload (a second publish with the same id fails with
           the UploadError message); a bad title with upload_id does not consume the upload; without an UploadStore the
           tool is absent and upload_id still gives the "not available" error.
         - tests/unit/test_http_app.py: the upload route answers without a bearer token and /mcp does not.
      6. Full suite at 100% coverage.
      Stop with status=blocked if Starlette buffers the whole request body before handle_put runs (the streamed 413 test
      then shows the full body read), or if custom routes cannot take a `{upload_id}` path parameter.
    acceptance:
      - python3 -m pytest tests/unit/test_uploads.py tests/unit/test_server.py tests/unit/test_http_app.py -q passes
      - python3 -m pytest -q --cov=src/html_artifact_deploy --cov-branch --cov-report=json:coverage.json && python3 scripts/check_coverage_floor.py coverage.json passes
      - python3 scripts/mypy_strict_modules.py --list | grep -c "src/html_artifact_deploy/" prints 13
      - ruff check . && ruff format --check . && python3 scripts/mypy_strict_modules.py && bandit -q -c pyproject.toml -r src pass

  - id: p8-url-fetch
    title: Publish from an allowlisted https address (publish_page_from_url)
    depends_on: [p7-uploads]
    complexity: M
    touches:
      - src/html_artifact_deploy/fetch_client.py
      - src/html_artifact_deploy/config.py
      - src/html_artifact_deploy/server.py
      - src/html_artifact_deploy/__main__.py
      - src/html_artifact_deploy/http_app.py
      - deploy/config.example.toml
      - pyproject.toml
      - scripts/check_coverage_floor.py
      - tests/unit/test_fetch_client.py
      - tests/unit/test_config.py
      - tests/unit/test_server.py
      - tests/unit/test_main.py
      - tests/unit/test_http_app.py
    brief: |
      Read docs/publish-pages-plan.md sections D2 (the [fetch] section, FetchConfig, HOST_RE and the rows marked (p8)),
      D7 (check_can_publish), D8 and D16 first; they are the spec. Do not edit CHANGELOG.md.
      1. config.py: add FetchConfig, Config.fetch, HOST_RE, `fetch` to the known sections, and the four (p8) checks of the
         D2 table, after the [auth] checks, in that order and with those messages.
      2. Create src/html_artifact_deploy/fetch_client.py exactly as D16 (constants, FetchClientError, FetchClient with
         check_url, fetch, _fetch_blocking, and the private _AllowlistRedirectHandler).
      3. server.py: add `fetcher: FetchClient | None = None` to AppContext; register publish_page_from_url with D16's
         docstring and annotations only when app.fetcher is not None, with D16's flow; insert D8's p8 sentence into
         publish_page's docstring.
      4. __main__.py (stdio) and http_app.py: build `FetchClient(config.fetch, config.pages.max_bytes)` when config.fetch is
         set and pass it as AppContext.fetcher.
      5. deploy/config.example.toml: add the [fetch] section from D2 with a comment that removing it turns the tool off.
      6. pyproject.toml: add "html_artifact_deploy.fetch_client" to the mypy override's module list.
         scripts/check_coverage_floor.py: add fetch_client.py at 100.0 with the comment "# which hosts the server
         downloads from (SSRF boundary)".
      7. Tests (marker `unit`). Network is always faked: pass `resolve=` returning fixed getaddrinfo tuples and
         `handlers=[FakeHTTPS(...)]`, a urllib.request.HTTPSHandler subclass whose https_open returns
         `urllib.response.addinfourl(io.BytesIO(body), headers, req.full_url, code)` with `resp.msg = "OK"` set, from a
         dict of url → (code, headers, body), recording every request it sees.
         - tests/unit/test_fetch_client.py: success returns the bytes and sends USER_AGENT and no Authorization or Cookie
           header; every check_url row with the exact message: http scheme, `https://user:pass@h/`, `https://@h/`, a tab
           or newline inside the URL, `https://[::1/`, port `:abc`, port 8443, `https://h:443/` accepted, no host, a host
           not listed, a getaddrinfo OSError, resolved 10.0.0.5, 127.0.0.1, 169.254.169.254, ::1 (an IPv6 tuple
           `(AF_INET6, SOCK_STREAM, 6, "", ("::1", 443, 0, 0))`), fe80::1%eth0 and 64:ff9b::a00:1 each refused, a public
           93.184.216.34 accepted; an HTTPS_PROXY set in the environment is ignored (monkeypatch.setenv it; the fake still
           receives the request); a redirect to another allowed host is followed; a redirect to a non-allowed host and to
           http:// raise the "redirected to" message and the fake never sees the second request; `Location: https:///x`
           gives "redirected to an invalid address"; a malformed `Location: https://[::1/` gives "Could not fetch the page
           from"; a `file:///etc/passwd` Location and an A→B→A loop each give "answered HTTP 302."; three redirects
           succeed with redirect_request seeing counts 0, 1, 2, and a fourth raises "redirected too many times."; a 404 →
           "answered HTTP 404." and its body is closed; an OSError from the fake → "Could not fetch the page from";
           Content-Length over max_bytes is refused before reading (the fake's body reports that read() was not called);
           a non-numeric Content-Length is ignored; a body one byte over max_bytes without Content-Length is refused and
           exactly max_bytes is accepted; the log line names the host and not the path or query (caplog with a
           `?X-Amz-Signature=secret` URL); fetch() runs through asyncio.to_thread (await it in an async test).
         - tests/unit/test_config.py: one parametrized case per (p8) row; a valid [fetch] loads with lowercased hosts and
           the default timeout 20; the example file still loads.
         - tests/unit/test_server.py: without a fetcher the tool is absent; with a FetchClient over the fake: the tool is
           listed with D16's annotations; publishing from a URL writes the file and returns the publish_page fields;
           page_id replacement keeps the url; a bad title and another owner's page_id fail without any request reaching
           the fake; a FetchClientError message comes back as the tool error; a fetched body that is not UTF-8 gives
           D7's message.
         - tests/unit/test_main.py and tests/unit/test_http_app.py: with [fetch] in the config, the built AppContext has a
           fetcher (stdio: capture build_server's argument via monkeypatch; HTTP: tools/list includes
           publish_page_from_url).
      8. Full suite at 100% coverage.
      Stop with status=blocked if urllib does not call redirect_request for a 302 from the fake handler, or if
      req.redirect_dict does not count the hops as D16 assumes (see Risks).
    acceptance:
      - python3 -m pytest tests/unit/test_fetch_client.py tests/unit/test_config.py tests/unit/test_server.py tests/unit/test_main.py tests/unit/test_http_app.py -q passes
      - python3 -m pytest -q --cov=src/html_artifact_deploy --cov-branch --cov-report=json:coverage.json && python3 scripts/check_coverage_floor.py coverage.json passes
      - python3 scripts/mypy_strict_modules.py --list | grep -c "src/html_artifact_deploy/" prints 14
      - grep -c "nosec" src/html_artifact_deploy/fetch_client.py prints 0
      - ruff check . && ruff format --check . && python3 scripts/mypy_strict_modules.py && bandit -q -c pyproject.toml -r src pass

  - id: p9-deployment-docs
    title: systemd unit, Caddyfile, deployment and configuration guides, README
    depends_on: [p8-url-fetch]
    complexity: M
    touches:
      - deploy/html-artifact-deploy.service
      - deploy/Caddyfile.example
      - docs/deployment.md
      - docs/configuration.md
      - docs/README.md
      - README.md
      - tests/unit/test_deploy_files.py
    brief: |
      Read docs/publish-pages-plan.md sections D2, D11, D12, D13, D14, D15, D16 first; they are the spec. Do not edit
      CHANGELOG.md. Standing docs describe today's behaviour only, with no project history (ADR 0008); where a "why" is
      needed, cite ADRs 0011–0020 by number (p10 writes them; their numbers are fixed by the plan's ADRs section).
      1. deploy/html-artifact-deploy.service and deploy/Caddyfile.example exactly as D14.
      2. docs/deployment.md: the D14 walkthrough, numbered, every command in a code block. Google steps link
         https://console.cloud.google.com/auth/branding, https://console.cloud.google.com/auth/audience (choose Internal)
         and https://console.cloud.google.com/auth/clients/create. Commands that touch state.dir run as the service user
         exactly as D13 says. A section "Large pages" explains the three ways a page arrives and their limits: as the
         html argument (limited by what the model can write in one tool call; pages.max_bytes on the server), through
         create_page_upload (needs a client that can make an HTTP PUT, such as Claude Code; pages.max_bytes, 15 minutes,
         10 open uploads per person), and through publish_page_from_url (only hosts in fetch.allowed_hosts; https only;
         pages.max_bytes; fetch.timeout_seconds; why the list exists, citing ADR 0020).
      3. docs/configuration.md: one table per section of D2 (key, type, required/default, meaning), the environment
         variables (HTML_ARTIFACT_DEPLOY_CONFIG and the client-secret variable) and the D13 commands.
      4. docs/README.md: add deployment.md and configuration.md under "Using HTML Artifact Deploy".
      5. README.md: rewrite "Connect a client" as D15 (four subsections), add a short "Run your own server" paragraph
         linking docs/deployment.md, and add rows to the tool table for create_page_upload ("HTTP only") and
         publish_page_from_url ("only when [fetch] is configured").
      6. tests/unit/test_deploy_files.py (marker `unit`): every key in deploy/config.example.toml appears in
         docs/configuration.md (parse the TOML, then search the doc text for each `key`); the Caddyfile's pages site
         contains the exact Content-Security-Policy value from D14 and no `browse`; the systemd unit contains ExecStart with
         `serve-http`, EnvironmentFile, ReadWritePaths=/srv/pages and NoNewPrivileges=yes; docs/deployment.md contains
         the strings "create_page_upload", "publish_page_from_url" and "fetch.allowed_hosts".
      7. python3 -m pytest tests/unit/test_no_project_history.py -q must pass.
      Stop with status=blocked if a D13 command or a D2 key the docs must describe does not exist in the code as merged.
    acceptance:
      - python3 -m pytest tests/unit/test_deploy_files.py tests/unit/test_no_project_history.py -q passes
      - grep -c "Content-Security-Policy \"sandbox allow-scripts" deploy/Caddyfile.example prints 1
      - grep -c "docs/deployment.md" README.md prints at least 1
      - python3 -m pytest -q passes

  - id: p10-adrs-and-retire
    title: ADRs 0011–0020, changelog, reference-doc touch-ups, delete the plan
    depends_on: [p9-deployment-docs]
    complexity: M
    touches:
      - docs/adr/0011-a-standalone-server-that-writes-into-the-web-servers-folder.md
      - docs/adr/0012-pages-are-served-from-a-separate-sandboxed-host-name.md
      - docs/adr/0013-a-page-link-is-a-random-id-plus-a-slug-and-ownership-is-an-index.md
      - docs/adr/0014-the-server-is-its-own-oauth-server-and-google-only-proves-identity.md
      - docs/adr/0015-client-registration-accepts-only-allowlisted-redirect-uris.md
      - docs/adr/0016-google-id-tokens-are-trusted-by-tls-not-by-signature.md
      - docs/adr/0017-all-state-is-one-sqlite-file.md
      - docs/adr/0018-large-pages-arrive-through-one-time-upload-urls.md
      - docs/adr/0019-streamable-http-is-stateless-and-starlette-and-uvicorn-are-declared.md
      - docs/adr/0020-pages-are-fetched-only-from-allowlisted-https-hosts.md
      - docs/adr/README.md
      - docs/live-qa.md
      - docs/testing-policy.md
      - CHANGELOG.md
      - docs/publish-pages-plan.md
      - docs/publish-pages-plan-manual-steps.html
    brief: |
      1. Write ADRs 0011–0020 from the plan's "ADRs" list, with the file names in `touches` and docs/adr/README.md's
         template. Context and Decision come from the matching D-section; "Alternatives considered" from the plan's
         "Rejected alternatives" bullets for that decision; Verification names the enforcing files and tests (for example
         0012 → config.py's host check and tests/unit/test_config.py, deploy/Caddyfile.example and
         tests/unit/test_deploy_files.py). Status `Accepted — <today's date>.` Related: source files, and ADRs 0003,
         0007 and 0010 where relevant; never the plan document.
      2. docs/adr/README.md: add the ten rows to the index.
      3. docs/live-qa.md: a short section saying the google_openid_configuration check needs no QA account or
         credentials, so it can run anywhere (it still runs weekly on the runner with the others).
      4. docs/testing-policy.md: in "Layer 5", one sentence naming google_openid_configuration as the only live check and
         what it guards (Google's authorization and token endpoints).
      5. CHANGELOG.md under ## [Unreleased]: ### Added, replacing the skeleton line: the publish_page, list_pages and
         unpublish_page tools; Google Workspace sign-in for claude.ai, Claude Desktop and Claude Code over Streamable
         HTTP; API tokens; create_page_upload; publish_page_from_url for allowlisted hosts; stdio with --config; the deploy/ examples and the deployment and
         configuration guides. ### Removed: the placeholder echo tool.
      6. git rm docs/publish-pages-plan.md docs/publish-pages-plan-manual-steps.html; grep -rn "publish-pages-plan" .
         --exclude-dir=.git must return nothing.
      7. Dispatch build.yml against this phase branch and report the run.
      Stop with status=blocked if an ADR number 0011–0020 is already taken on main when you start (renumbering is then
      the orchestrator's call, since p9's docs cite the numbers).
    acceptance:
      - ls docs/adr/00{11,12,13,14,15,16,17,18,19,20}-*.md lists ten files
      - grep -cE "^\| \[00(1[1-9]|20)\]" docs/adr/README.md prints 10
      - test ! -e docs/publish-pages-plan.md && test ! -e docs/publish-pages-plan-manual-steps.html
      - grep -rn "publish-pages-plan" . --exclude-dir=.git returns nothing
      - python3 -m pytest -q passes (including test_no_project_history)
      - build.yml dispatched against the phase branch is green (run URL in PHASE-REPORT)
```
