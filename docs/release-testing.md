# Release testing

Everything a human checks before a release: the gates in the order they run, then the manual
checks. What CI proves automatically is in [`testing-policy.md`](testing-policy.md); how a tag is
cut is in [`releasing.md`](releasing.md) and `/cut-release`.

## What stays manual

A check stays manual only when automation cannot reliably decide pass or fail:

- how the tool descriptions read, and whether a real model picks the right tool, in one real
  client per supported client family — run only when the tool list or its descriptions changed;
- a first-time consent or OAuth screen, which the upstream provider renders;
- review of the upstream drift the live check detected.

Nothing is added to this list unless it meets that bar. Do not rerun the automated suite by hand
because a release is being cut.

## Gates, in order

1. **CI green on the commit to tag**, with every required check passing. If the release touched an
   upstream client, the latest `live-check.yml` run is green.
2. **`scripts/pre_release_check.py`** from the repo root: CI's blocking commands, PASS/FAIL each.
3. **`build.yml` pre-flight.** Dispatch it (no inputs) against the exact commit to tag; every job
   before the publish jobs succeeds. It publishes nothing.
4. **Human checks** below, against that run's wheel.
5. **`release.yml` dry run** with `version` and `dry_run: true`. Read the rendered notes.
6. **Cut.** Re-dispatch with `dry_run: false`.

## Human checks

With the pre-flight's wheel installed in a clean environment:

- [ ] Connect it to Claude Code (`claude mcp add ...` as the README says) and to Claude Desktop;
      both list every tool.
- [ ] Ask each client to do one task per tool in plain words; it picks the right tool without
      being told its name.
- [ ] One failing call (a bad argument, an upstream error) shows the user a useful message and no
      stack trace or credential.
