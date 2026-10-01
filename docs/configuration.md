# Configuration

The server reads one TOML file. Its path is the `--config PATH` option, else the environment variable
`HTML_ARTIFACT_DEPLOY_CONFIG`, else `/etc/html-artifact-deploy/config.toml`. A commented example is
[`deploy/config.example.toml`](../deploy/config.example.toml).

An unknown section or key is an error, not ignored, so a typo in a security setting cannot be silently
dropped. Every error names the file and the key. Where a value is an `https://` address, it must have no
query, fragment or user name, and a trailing `/` is dropped.

## `[pages]` (required)

| Key | Type | Required / default | Meaning |
|---|---|---|---|
| `root` | string | required | Absolute path of the folder the web server publishes. Each page is `<root>/<page id>/index.html`. |
| `public_base_url` | string | required | The `https://` address at which the web server publishes `root`. It must not share a host name with `http.public_url` (ADR 0012). |
| `max_bytes` | integer | `16000000` | The largest page in bytes, 1 to 50000000. |
| `default_expiry_days` | integer | `90` | Days a new page lives unless its publisher asks for another number, 1 to `max_expiry_days` (ADR 0021). |
| `max_expiry_days` | integer | `365` | The longest a page may live, in days, 1 to 365. |

## `[state]` (required)

| Key | Type | Required / default | Meaning |
|---|---|---|---|
| `dir` | string | required | Absolute path of the folder where the server keeps its database. |

## `[stdio]`

| Key | Type | Required / default | Meaning |
|---|---|---|---|
| `user` | string | `"local"` | The name that pages published over stdio are owned by. At most 200 characters. |

## `[http]` (required for `serve-http` and `token`)

| Key | Type | Required / default | Meaning |
|---|---|---|---|
| `public_url` | string | required | The `https://` address of this server, as clients reach it. |
| `listen_host` | string | `"127.0.0.1"` | The address to listen on. Keep it on the loopback address and let Caddy face the network. |
| `listen_port` | integer | `8765` | The port to listen on, 1 to 65535. |

## `[auth]` (required for `serve-http` and `token`)

| Key | Type | Required / default | Meaning |
|---|---|---|---|
| `google_client_id` | string | required | The Client ID of your Google OAuth client, ending in `.apps.googleusercontent.com`. |
| `google_client_secret_env` | string | `"HTML_ARTIFACT_DEPLOY_GOOGLE_CLIENT_SECRET"` | The name of the environment variable that holds the Google client secret. The secret itself is never kept in this file. |
| `allowed_domains` | list of strings | required unless `allowed_emails` is given | Google Workspace domains whose accounts may sign in. Google's hosted-domain claim decides, not the e-mail address. |
| `allowed_emails` | list of strings | required unless `allowed_domains` is given | Individual e-mail addresses that may sign in. |
| `redirect_uri_allowlist` | list of strings | claude.ai and claude.com callbacks | The `https://` redirect addresses OAuth clients may register (ADR 0015). Loopback addresses are always accepted. |

## `[fetch]` (optional)

Turns on the `publish_page_from_url` tool (ADR 0020). Remove the section to turn the tool off.

| Key | Type | Required / default | Meaning |
|---|---|---|---|
| `allowed_hosts` | list of strings | required | The host names pages may be fetched from. Plain names only: no addresses, ports or wildcards, and not this server's own host names. |
| `timeout_seconds` | integer | `20` | Seconds to wait for each network operation, 1 to 120. |

## Environment variables

| Variable | Meaning |
|---|---|
| `HTML_ARTIFACT_DEPLOY_CONFIG` | The configuration file's path, used when `--config` is not given. |
| `HTML_ARTIFACT_DEPLOY_GOOGLE_CLIENT_SECRET` | The Google OAuth client secret. The variable's name can be changed with `auth.google_client_secret_env`. Only `serve-http` and `check-config` read it. |

## Commands

```
html-artifact-deploy [--config PATH] [--version]
html-artifact-deploy [--config PATH] serve-http
html-artifact-deploy [--config PATH] check-config
html-artifact-deploy [--config PATH] token create --user EMAIL --name NAME [--days N]
html-artifact-deploy [--config PATH] token list
html-artifact-deploy [--config PATH] token revoke --user EMAIL --name NAME
html-artifact-deploy [--config PATH] purge-expired
```

`--config` goes before the subcommand. Without a subcommand the server speaks MCP over stdio. Every
error prints `html-artifact-deploy: <message>` on stderr and exits with `2`.

- `serve-http` serves the tools over Streamable HTTP with Google sign-in. It needs `[http]` and `[auth]`
  and the client secret.
- `check-config` validates the file, the pages folder and the state folder, and prints
  `Configuration OK: <path>`. When `[auth]` is present it also needs the client secret.
- `token create` makes an API token for a person, for clients that cannot do the browser sign-in. The
  address must match `auth.allowed_emails` or `auth.allowed_domains`. `--days` defaults to `365` (1 to
  3650). The token is printed alone on stdout, once; it is not shown again.
- `token list` prints one tab-separated line per token: user, name, expiry, creation time.
- `token revoke` removes one token and prints `Revoked.`.
- `purge-expired` deletes expired pages now and prints `Deleted <n> expired pages.` It needs neither
  `[http]` nor the secret.

On a server, run every command as the service user so that the files in `state.dir` keep one owner:

```bash
sudo -u html-artifact-deploy /opt/html-artifact-deploy/bin/html-artifact-deploy \
  --config /etc/html-artifact-deploy/config.toml token create --user you@example.com --name laptop
```

`check-config` also needs the secret, so it runs through `systemd-run`, as shown in
[`deployment.md`](deployment.md).
