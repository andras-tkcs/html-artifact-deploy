# Live QA: the upstream service, for real

Unit tests replay redacted fixtures. This document is about the checks that talk to the real
upstream service, which is layer 5 of [`testing-policy.md`](testing-policy.md).

## Accounts

Use a dedicated QA account (or tenant) on the upstream service, never a personal or production
one. Seed it with the objects the checks read, and write their identifiers into
`tests/fixtures/qa_environment.yaml` on the QA machine only; that file is git-ignored by name in the
runner's checkout and never committed.

## The self-hosted runner

Live credentials live only on a self-hosted runner the project controls (ADR 0007):

1. On a machine you control, add a runner under **Settings → Actions → Runners → New self-hosted
   runner**, with the extra label `html-artifact-deploy-qa`. Run it as its own OS user.
2. Authenticate the QA account on that machine, storing the credential in a file outside the
   checkout (for example `~/.config/html-artifact-deploy-qa/`), readable only by the runner's user.
3. Tell `scripts/live_check.py`'s `fetch` functions where to find it through an environment
   variable set in the runner's `.env` file, not in a workflow.
4. Uncomment the `schedule:` trigger in `.github/workflows/live-check.yml`.

`live-check.yml` and `qa-record-fixture.yml` run only on `schedule` and `workflow_dispatch`, never
on `pull_request`, so a fork's code never runs where the credentials are. Both share one
concurrency group, because they read and refresh the same credential files.

## Recording one fixture

```
python3 scripts/live_check.py --record <name>      # on a QA machine
```

or dispatch `qa-record-fixture.yml` with `name=<name>` against your branch (`/qa-record <name>`
does this and pulls the result). Then **read the diff**: identity fields must already be
placeholders. A real e-mail, name, account ID, tenant URL or token means `redact` needs fixing
before anything is committed.

## Checking for drift

```
python3 scripts/live_check.py --check              # every check; never writes
```

It compares the shape of each live response with its fixture and reports the fixture's age:
under 60 days healthy, 60–90 a warning, over 90 a refresh is required.
