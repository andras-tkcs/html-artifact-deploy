# ADR 0020: Pages are fetched only from allowlisted https hosts

## Status

Accepted — 2026-10-01. Implemented; off unless `[fetch]` is configured.

## Context

A page may already be online (a raw file on a Git host, a presigned storage link). Letting the
server download it avoids passing it through the conversation. But a server that fetches any
address a model supplies can be steered at cloud metadata endpoints or internal services
(server-side request forgery).

## Decision

`publish_page_from_url` exists only when the administrator writes a `[fetch]` section with
`allowed_hosts`. The server fetches over https only, with no user name, password or port, from a
listed host name only (never an IP address, never this server's own names). It resolves the name
and refuses any address that is not globally routable, including NAT64 ranges. It connects
directly, never through a proxy (`ProxyHandler({})`), because a proxy would resolve the name
itself. Redirects are followed at most three times. Each redirect target is first resolved by
urllib against the request URL (so a target like `https:///x` is turned into an address before
the check) and every hop's final URL is checked against the allowlist and the address rules. The
size is capped at `pages.max_bytes`, and the path and query are never logged, since a presigned
query is a credential.

## Alternatives considered

- **Fetching any URL.** Open to server-side request forgery.
- **Fetching through `httpx2`.** It comes with `mcp` but is not a declared dependency and may
  change name with the SDK; `urllib` is standard library and its redirect handler is the one hook
  needed.
- **Fetching claude.ai artifacts by link.** An artifact's link serves a viewer page and is private
  by default, so there is no raw file.

## Consequences

Accepted risks: the connection resolves the name again after the check (a DNS re-resolution
window), tolerated because only administrator-listed names reach it; and a network that reaches the
internet only through a proxy cannot use the tool.

## Verification

`src/html_artifact_deploy/fetch_client.py`, tested in `tests/unit/test_fetch_client.py` (redirects
to refused hosts, redirects without a host, the redirect limit, private addresses); the
`[fetch]` rules in `src/html_artifact_deploy/config.py` (`tests/unit/test_config.py`).

## Related

ADR 0003, ADR 0012, ADR 0018.
