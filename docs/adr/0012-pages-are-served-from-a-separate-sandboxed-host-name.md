# ADR 0012: Pages are served from a separate, sandboxed host name

## Status

Accepted — 2026-10-01. Implemented.

## Context

A published page is arbitrary HTML with arbitrary scripts, written by a person and opened by
others. The OAuth server holds sign-in cookies and issues tokens. If a page's scripts ran on the
same origin as the OAuth server, a page could read or abuse them.

## Decision

Pages are served from their own host name (`pages.public_base_url`), never the server's. The
configuration refuses a `pages.public_base_url` whose host equals `http.public_url`'s host, and
refuses a `fetch.allowed_hosts` entry naming either of this server's own hosts. The pages site
adds `Content-Security-Policy: sandbox ...`, which gives every page an opaque origin, and lists no
directory.

## Alternatives considered

- **Serving pages from the OAuth server's host under a path.** Same origin as the sign-in
  endpoints; rejected.
- **Serving pages from this process.** See ADR 0011.
- **No sandbox header.** A separate host name already isolates cookies, but the header also keeps
  one page from reaching another page's storage on the same host.

## Consequences

Two DNS names and two certificates are required. Pages can run scripts, open pop-ups and submit
forms, but cannot reach each other's or the server's origin.

## Verification

`src/html_artifact_deploy/config.py` (the host comparison in `load_config`), tested in
`tests/unit/test_config.py`. `deploy/Caddyfile.example` sets the header and disables directory
listing, checked by `tests/unit/test_deploy_files.py`.

## Related

ADR 0011, ADR 0013.
