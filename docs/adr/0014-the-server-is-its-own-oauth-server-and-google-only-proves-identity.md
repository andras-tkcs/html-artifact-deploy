# ADR 0014: The server is its own OAuth server, and Google only proves identity

## Status

Accepted — 2026-10-01. Implemented.

## Context

claude.ai and Claude Desktop connect to a remote MCP server through the MCP authorization flow,
which expects the server's own OAuth metadata, dynamic client registration and PKCE. The
organization wants people to sign in with their Google Workspace account, and to be able to cut
off someone removed from the allowlist.

## Decision

The server is its own OAuth 2.1 authorization server (the SDK's routes, backed by
`GoogleOAuthProvider`). Google is used only to prove who the person is; Google's tokens are never
handed to clients. The server issues its own opaque access, refresh and API tokens. Refresh
tokens have an absolute lifetime (a rotated token keeps the old expiry), and the allowlist
(`auth.allowed_domains` / `allowed_emails`) is re-checked on every refresh and every request.

Storage: tokens, authorization codes and sign-in states are stored only as hashes
(`secret_hash`), so a copy of the database grants no access. A registered client's secret is the
exception: it is stored in plain text in `oauth_clients.info_json`, because the SDK compares client
secrets in plain text.

## Alternatives considered

- **Letting Caddy or an identity-aware proxy handle sign-in.** claude.ai needs the MCP server's
  own metadata, registration and PKCE, which a proxy does not provide.
- **Any OIDC provider.** Google Workspace was chosen and `hd` is Google-specific; a second
  provider would be a new `*_oidc.py` beside the first.
- **JWT access tokens.** They cannot be revoked without a deny list; opaque tokens in SQLite can.
- **Refresh tokens that renew their own lifetime.** Someone removed from the allowlist would keep
  access by refreshing forever.

## Consequences

Revocation is immediate (delete the row). A person signs in with Google at least every 30 days. A
database copy yields client secrets, but no usable token.

## Verification

`src/html_artifact_deploy/oauth_provider.py` and `src/html_artifact_deploy/token_store.py`, tested
in `tests/unit/test_oauth_provider.py` and `tests/unit/test_token_store.py` (including that no
token, code or sign-in secret reaches the database file in plain text);
`src/html_artifact_deploy/google_oidc_client.py`; `is_allowed_email` in
`src/html_artifact_deploy/config.py`.

## Related

ADR 0003, ADR 0015, ADR 0016, ADR 0017.
