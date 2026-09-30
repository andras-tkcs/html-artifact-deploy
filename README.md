# HTML Artifact Deploy

An MCP server that deploys HTML artifacts.

## Install

```bash
pip install html-artifact-deploy
```

## Connect a client

**Claude Code**

```bash
claude mcp add html-artifact-deploy -- html-artifact-deploy
```

**Claude Desktop** (`claude_desktop_config.json`)

```json
{
  "mcpServers": {
    "html-artifact-deploy": { "command": "html-artifact-deploy" }
  }
}
```

## Tools

| Tool | What it does |
|---|---|
| `echo` | Returns its input (a placeholder; replace with the real tools) |
| `server_info` | Reports the server's name and version |

## Documentation

- [`docs/README.md`](docs/README.md) — index of the user and contributor docs.
- [`CONTRIBUTING.md`](CONTRIBUTING.md) — how to contribute.
- [`CHANGELOG.md`](CHANGELOG.md) — what changed in each release.
