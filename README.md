# HTML Artifact Deploy

An MCP server that deploys HTML artifacts.

## Install

```bash
pip install html-artifact-deploy
```

## Connect a client

**Claude Code**

```bash
claude mcp add html-artifact-deploy -- html-artifact-deploy --config ~/html-artifact-deploy.toml
```

**Claude Desktop** (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "html-artifact-deploy": { "command": "html-artifact-deploy", "args": ["--config", "~/html-artifact-deploy.toml"] }
  }
}
```

## Tools

| Tool | What it does |
|---|---|
| `publish_page` | Publishes a single-file HTML page and returns its link |
| `list_pages` | Lists the pages you published, newest first |
| `unpublish_page` | Takes one of your pages down |
| `server_info` | Reports the server's name and version |

## Documentation

- [`docs/README.md`](docs/README.md) — index of the user and contributor docs.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — how to contribute.
- [`CHANGELOG.md`](CHANGELOG.md) — what changed in each release.
