# ADR 0018: Large pages arrive through one-time upload URLs

## Status

Accepted — 2026-10-01. Implemented.

## Context

A page can be megabytes, and passing it as a tool argument makes the model write it into the
conversation twice. Claude Code and other clients with a shell can send a file with an HTTP PUT
instead.

## Decision

`create_page_upload` returns a one-time URL, `<public_url>/uploads/<id>`. The id is a random
secret, stored only as a hash, and the URL is the capability: the PUT needs no bearer token. A slot
expires after 15 minutes, is filled once, is limited to `pages.max_bytes`, and belongs to the
person who created it (at most ten open per person). `publish_page` then takes the `upload_id`
instead of the HTML, and the server reads and discards the file. Uploads are streamed into a
temporary file and the size is counted as it arrives. The HTTP server does not log request lines.

## Alternatives considered

- **Raising the tool-argument size only.** The page would still have to be written out by the
  model, in full, in the conversation.

## Consequences

A client without a way to make an HTTP request cannot use it and falls back to `publish_page` with
`html`, or to `publish_page_from_url` (ADR 0020). The upload URL is a secret and must not be
logged.

## Verification

`src/html_artifact_deploy/uploads.py`, tested in `tests/unit/test_uploads.py`; the route in
`src/html_artifact_deploy/http_app.py` (`tests/unit/test_http_app.py`).

## Related

ADR 0017, ADR 0019, ADR 0020.
