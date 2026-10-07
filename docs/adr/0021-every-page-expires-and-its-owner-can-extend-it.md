# ADR 0021: Every page expires, and its owner can extend it

## Status

Accepted — 2026-10-01. Implemented.

## Context

The pages folder only grows, and nobody notices a forgotten page until the disk fills. Some pages
are still in use long after they were published.

## Decision

Every page expires: 90 days by default (`pages.default_expiry_days`), at most 365
(`pages.max_expiry_days`), both configurable downwards. A publisher may ask for 1 to the maximum;
replacing a page keeps its expiry unless one is given. The owner can move the expiry at any time
with `extend_page_expiry`, to between 1 and the maximum days from now (earlier is allowed; the tool
is not marked destructive because nothing is deleted by the call). An expired page disappears from
its owner's list at once; its files are deleted within about an hour, by a purge after each
publish or by the `purge-expired` command run from an hourly systemd timer. A failed deletion keeps
the row and is retried later; a purge failure never fails a publish.

## Alternatives considered

- **Pages that never expire.** The folder only grows.
- **Expiry fixed at publish time.** It forces a re-publish, and a new link, to keep a page in use;
  `extend_page_expiry` keeps the link.
- **A background task inside the server for purging.** It needs its own scheduling and shutdown
  inside the SDK's app lifespan; a purge after each publish plus an hourly timer is simpler, and
  the index already hides expired pages in between.

## Consequences

An expired page cannot be extended; publishing it again gives a new link. A deployment must run
the purge timer, or pages are removed only when someone publishes.

## Verification

`src/html_artifact_deploy/service.py` (`extend`, `purge_expired`), tested in
`tests/unit/test_service.py`; the tools in `src/html_artifact_deploy/server.py`; `purge-expired` in
`src/html_artifact_deploy/__main__.py` (`tests/unit/test_main.py`); the timer and service in
`deploy/`, checked by `tests/unit/test_deploy_files.py`.

## Related

ADR 0013, ADR 0017.
