# ADR 0003: Use the official MCP Python SDK, not a hand-rolled transport

## Status

Accepted — 2026-09-30. Inherited from the MCP server template.

## Context

The server speaks the Model Context Protocol over stdio, and possibly Streamable HTTP later. The
protocol's framing is simple enough to hand-roll, and the project prefers the standard library
over dependencies. But the specification is still moving, it is security-relevant once HTTP and
authorization are involved, and what matters is conformance with real clients, not with our
reading of the spec.

## Decision

The official `mcp` SDK is a runtime dependency and serves the protocol (`mcp.server.mcpserver`).
This is a deliberate, named exception to "standard library first". The pin is a major-version
range (`mcp>=2.2,<3.0.0`), because a new major version of the SDK changes the server API.

## Alternatives considered

- **Hand-rolled JSON-RPC over stdio.** Smaller footprint, but it means owning spec drift
  indefinitely, and a full test surface for a transport the SDK already maintains and tests.
- **A third-party framework on top of the SDK.** Rejected until a concrete need appears: one more
  dependency tracking the same moving specification.

## Consequences

- The project tracks the SDK's releases, including a major-version migration when one comes.
- Tests drive the server with the SDK's own client (`mcp.Client`), in process and over stdio, so
  an SDK upgrade that changes behavior fails the suite rather than a user's client.
- The "standard library first" rule still governs everything else.

## Verification

`pyproject.toml`'s `dependencies`; `src/html_artifact_deploy/server.py`; `tests/unit/test_server.py` and
`tests/integration/test_stdio_contract.py`.
