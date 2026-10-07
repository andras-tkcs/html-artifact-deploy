# ADR 0013: A page's link is only a random id, and ownership is an index

## Status

Accepted — 2026-10-01. Implemented.

## Context

Anyone with a page's link may open it, so the link is the only protection against strangers
finding a page. Who published a page, and which pages a person can replace or take down, has to
be recorded somewhere.

## Decision

A page's address is `<public_base_url>/<32 lowercase hex characters>/`: 128 random bits from
`secrets.token_hex(16)`, with no file name or title in it. The title lives only in the index.
Ids are checked for uniqueness: the page folder is created with an exclusive `mkdir` and the index
row is a plain insert against the primary key; a clash draws a new id, at most three times.
Ownership is one row per page in the state database, keyed by id, with the owner's e-mail address.
The pages folder is never listed, by the server or by the web server.

## Alternatives considered

- **A readable name in the link (`/<id>/q3-report.html`).** It leaks what the page is about to
  anyone who sees the link, and adds a slug rule to maintain.
- **Per-person folders.** Reveals who published, and ownership is still needed for replacing.

## Consequences

Links are unguessable but not secret from whoever receives them: sharing is the access model.
Ownership checks happen in the index, not on disk. Titles never appear in an address.

## Verification

`src/html_artifact_deploy/pages.py` (`new_page_id`, `page_url`), tested in
`tests/unit/test_pages.py`; `src/html_artifact_deploy/storage.py` (exclusive `mkdir`) in
`tests/unit/test_storage.py`; `src/html_artifact_deploy/page_index.py` in
`tests/unit/test_page_index.py`; the id-retry loop in `src/html_artifact_deploy/service.py`
(`tests/unit/test_service.py`).

## Related

ADR 0012, ADR 0017.
