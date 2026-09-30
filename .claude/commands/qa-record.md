---
description: Record one live upstream fixture on the self-hosted runner and pull it back
argument-hint: "<name>  (one of CHECKS in scripts/live_check.py)"
---

Record the live fixture for: **$ARGUMENTS**

No live upstream credential exists on this machine and none ever will (ADR 0007): the QA
credentials live only on the self-hosted runner. So this does not run `scripts/live_check.py`
locally. It dispatches the workflow that can.

1. **Check the name is real** before dispatching: it must be a key of `CHECKS` in
   `scripts/live_check.py` on this branch. If no name was given above, ask me rather than guessing.
   If `CHECKS` is empty, stop and say the server has no live checks yet.

2. **Check the branch is pushed** (`git status -sb`): the workflow checks out the remote branch.

3. **Dispatch `qa-record-fixture.yml`** with `ref` set to the current branch and `name` set to
   that name. It runs on the self-hosted runner and commits the recorded fixture back to the
   branch it was dispatched against.

4. **Expect to queue.** It shares a concurrency group with `live-check.yml`, because both read and
   refresh the same credential files. A queued run is correct behaviour. Wait; do not re-dispatch,
   and do not cancel the run holding the group.

5. **When it finishes**, `git pull` so the committed fixture reaches this checkout, then **read the
   fixture diff** and confirm no real account identifier, e-mail, tenant URL, token or private
   content entered the repository (`docs/live-qa.md`). A fixture that arrived by dispatch gets
   exactly the same read as one recorded by hand.

6. **Report** the run URL, whether the fixture changed, and what you saw in the diff. If the run
   failed, give me the actual error from its log — an expired QA grant needs a person on the
   runner and is not something to retry.
