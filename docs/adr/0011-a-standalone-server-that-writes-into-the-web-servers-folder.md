# ADR 0011: A standalone server that writes pages into the web server's folder

## Status

Accepted — 2026-10-01. Implemented.

## Context

Claude (claude.ai, Claude Desktop, Claude Code) makes single-file HTML pages, and an organization
wants to publish them at a link anyone can open. Something has to take the page from a tool call
to a place a web server serves. That something could be a gateway connector, a remote uploader, or
the web server's own host.

## Decision

HTML Artifact Deploy is a standalone MCP server that runs on the same machine as the web server
and writes each page into the folder that web server serves (`pages.root`). It never serves pages
itself. Writing goes through the `PageStore` protocol, with `LocalFolderStore` as the only
implementation, so a remote backend can be added later without touching the tools.

## Alternatives considered

- **A connector inside an existing gateway (PrivacyFence).** It would make a gateway into a
  publisher with its own SSH key and page index, for something that governs no existing system.
- **SFTP to a remote web server.** It needs an SSH key pair, host-key pinning and a dependency
  with a copyleft licence. Running on the web server removes all of it; `PageStore` keeps the door
  open.
- **Serving pages from this process.** It would put untrusted page scripts on the OAuth server's
  origin and push page traffic through Python; a web server serves static files better (ADR 0012).
- **Adopting an existing project.** The candidates were young; one is AGPL with its single sign-on
  in a separately licensed folder, and the others lack claude.ai's OAuth or are multi-file site
  tools.

## Consequences

Deployment is one machine with a reverse proxy and this service; there is no key to manage. The
server needs write access to the pages folder and nothing else. A multi-machine deployment would
need a new `PageStore`. Ruled out: this process answering page requests.

## Verification

`src/html_artifact_deploy/storage.py` defines `PageStore` and `LocalFolderStore`, tested in
`tests/unit/test_storage.py`. No route in `src/html_artifact_deploy/http_app.py` returns a page; the
example web server configuration `deploy/Caddyfile.example` serves the folder, checked by
`tests/unit/test_deploy_files.py`.

## Related

ADR 0003 (the official MCP SDK), ADR 0010 (headless Linux only), ADR 0012.
