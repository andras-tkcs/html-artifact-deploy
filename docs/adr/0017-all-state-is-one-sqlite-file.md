# ADR 0017: All state is one SQLite file

## Status

Accepted — 2026-10-01. Implemented.

## Context

The server keeps the page index, OAuth clients, pending sign-ins, codes, tokens and upload slots.
Sign-ins, publishes and uploads can write at the same time, and some updates (marking a code or a
refresh token used) must happen exactly once.

## Decision

All server state lives in one SQLite file, `<state.dir>/state.sqlite3`, opened through the
standard library in WAL mode with foreign keys on. The schema version is `PRAGMA user_version`; a
file with an unknown version is refused at start-up rather than guessed at. Single-use rules are
compare-and-set updates (`UPDATE ... WHERE used_at IS NULL`) inside transactions. The state folder
is created with mode 0700, and an existing folder is tightened to 0700 when the file is opened;
the systemd units set `StateDirectoryMode=0700` as well.

## Alternatives considered

- **JSON files for state.** Several concurrent writers need transactions; SQLite is in the
  standard library.

## Consequences

Backup is copying one file (with the folder). One process per state folder. Schema changes need a
migration step keyed by `user_version`.

## Verification

`src/html_artifact_deploy/state.py` (`StateDB`, `SCHEMA_VERSION`), tested in
`tests/unit/test_state.py`; users in `src/html_artifact_deploy/page_index.py` and
`src/html_artifact_deploy/token_store.py`.

## Related

ADR 0013, ADR 0014, ADR 0018.
