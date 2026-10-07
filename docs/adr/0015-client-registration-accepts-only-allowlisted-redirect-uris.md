# ADR 0015: Client registration accepts only allowlisted redirect URIs

## Status

Accepted — 2026-10-01. Implemented.

## Context

Dynamic client registration is open: anyone can register a client. A stranger could register a
client whose redirect URI is their own site and send a signed-in person a link; the person's
authorization code would then arrive at the stranger. A second risk is a Google sign-in link
forwarded to someone else, who finishes it in their own browser.

## Decision

Registration accepts only redirect URIs that are exactly in `auth.redirect_uri_allowlist` (by
default claude.ai's and claude.com's callbacks) or are plain http to a loopback host (Claude
Code's local callback, any port and path). The check is repeated at authorization time. There is
no consent page. Each Google sign-in is bound to the browser that started it: `/oauth/google/start`
sets a cookie holding a random value whose hash is stored with the pending sign-in, and the
callback refuses a request whose cookie does not match. The cookie is `__Host-hda_signin` when
`http.public_url` is https and `hda_signin` over http (loopback testing only).

## Alternatives considered

- **A consent page after sign-in.** It asks the person to spot a hostile client; the allowlist
  removes that case outright. The binding cookie covers the remaining forwarded-link case.

## Consequences

A new MCP client with a non-loopback redirect URI needs an administrator to add it to the
allowlist. Opening the sign-in link in a different browser than the one the client opened fails
with a clear message.

## Verification

`is_allowed_redirect_uri`, `register_client`, `authorize`, `handle_google_start` and
`handle_google_callback` in `src/html_artifact_deploy/oauth_provider.py`, tested in
`tests/unit/test_oauth_provider.py`; the allowlist setting in `src/html_artifact_deploy/config.py`
(`tests/unit/test_config.py`).

## Related

ADR 0014.
