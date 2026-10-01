# ADR 0016: Google ID tokens are trusted by TLS, not by signature

## Status

Accepted — 2026-10-01. Implemented.

## Context

After the person signs in, the server receives an ID token from Google and reads the e-mail
address and hosted domain from it. A token can be validated by checking its signature against
Google's published keys, or trusted because of how it was received.

## Decision

The ID token is accepted because the server received it directly from Google's token endpoint over
TLS, with certificate validation, in exchange for the authorization code. This is what OpenID
Connect Core section 3.1.3.7 permits. The server still checks the nonce, issuer, audience and
expiry claims. It does not fetch or cache Google's signing keys. The authorization and token
endpoint addresses come from Google's discovery document, and a live check guards their shape.

## Alternatives considered

- **Verifying the signature with Google's JWKS.** It adds a key fetch, a key cache and a
  key-rotation failure mode for no gain over TLS for a token received directly from the issuer.

## Consequences

Fewer moving parts and no key-rotation failures. The decision does not apply to a token received
from anyone other than Google's token endpoint; such a path would need signature checking.

## Verification

`src/html_artifact_deploy/google_oidc_client.py`, tested in
`tests/unit/test_google_oidc_client.py` against the recorded discovery fixture;
`scripts/live_check.py` (`google_openid_configuration`).

## Related

ADR 0007 (live checks), ADR 0014.
