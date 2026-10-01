# HTML Artifact Deploy

An MCP server that deploys HTML artifacts: it publishes a single-file HTML page and returns a link.

## Install

```bash
pip install html-artifact-deploy
```

## Run your own server

The pages are published by a server your organization runs, on a machine with a web server in front.
[`docs/deployment.md`](docs/deployment.md) walks through it: Google sign-in, systemd, Caddy, large
pages and expiry. [`docs/configuration.md`](docs/configuration.md) lists every setting.

## Connect a client

In the examples, `https://publish.example.com` is your server.

**claude.ai and Claude Desktop**

Settings → Connectors → Add custom connector, URL `https://publish.example.com/mcp`. Sign in with
Google when asked.

**Claude Code, with sign-in**

```bash
claude mcp add --transport http html-artifact-deploy https://publish.example.com/mcp
```

Then run `/mcp` in Claude Code and sign in.

**Claude Code, with an API token**

An administrator creates the token on the server, as the service user:

```bash
sudo -u html-artifact-deploy /opt/html-artifact-deploy/bin/html-artifact-deploy \
  --config /etc/html-artifact-deploy/config.toml token create --user you@example.com --name laptop
```

Then:

```bash
claude mcp add --transport http html-artifact-deploy https://publish.example.com/mcp --header "Authorization: Bearer <token>"
```

**Local, over stdio**

For trying it out, with a configuration whose `pages.root` and `state.dir` are folders you own. Not
for a server deployment, whose folders belong to the service user.

```bash
claude mcp add html-artifact-deploy -- html-artifact-deploy --config ~/html-artifact-deploy.toml
```

For Claude Desktop, use the full path in `args`; JSON arguments are not expanded by a shell:

```json
{
  "mcpServers": {
    "html-artifact-deploy": { "command": "html-artifact-deploy", "args": ["--config", "/home/you/html-artifact-deploy.toml"] }
  }
}
```

## Tools

| Tool | What it does |
|---|---|
| `publish_page` | Publishes a single-file HTML page and returns its link |
| `create_page_upload` | Returns a one-time address to `PUT` a large page to, for `publish_page` (HTTP only) |
| `publish_page_from_url` | Publishes a page the server downloads from an https address (only when `[fetch]` is configured) |
| `extend_page_expiry` | Changes when one of your pages expires |
| `list_pages` | Lists the pages you published, newest first |
| `unpublish_page` | Takes one of your pages down |
| `server_info` | Reports the server's name and version |

## Documentation

- [`docs/README.md`](docs/README.md) — index of the user and contributor docs.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — how to contribute.
- [`CHANGELOG.md`](CHANGELOG.md) — what changed in each release.
