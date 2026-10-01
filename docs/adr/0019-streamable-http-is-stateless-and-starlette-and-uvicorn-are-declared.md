# ADR 0019: Streamable HTTP is stateless, and Starlette and uvicorn are declared

## Status

Accepted — 2026-10-01. Implemented.

## Context

The MCP SDK offers Streamable HTTP with or without per-client sessions, and with SSE streams or
plain JSON answers. The server runs behind a reverse proxy and restarts for upgrades. The SDK's
app is built on Starlette and run by uvicorn, both of which `mcp` already installs.

## Decision

Streamable HTTP runs with `stateless_http=True` and `json_response=True`: no MCP session lives in
the process, and every answer is plain JSON. DNS-rebinding protection is on, with the public host
and loopback as allowed hosts. `starlette` and `uvicorn` are declared as runtime dependencies,
because the code imports them directly.

## Alternatives considered

- **Stateful sessions.** Lost on restart, and a long-lived stream behind a reverse proxy for no
  benefit: the tools need no server-to-client messages.

## Consequences

A restart loses nothing; two requests need not reach the same process. Tools cannot push
notifications or ask the client questions.

## Verification

`src/html_artifact_deploy/http_app.py` (`build_http_app`), tested in
`tests/unit/test_http_app.py` and `tests/integration/test_http_contract.py`; the dependency
declarations in `pyproject.toml`.

## Related

ADR 0003 (the official MCP SDK), ADR 0018.
