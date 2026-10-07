# Architecture Decision Records

An **Architecture Decision Record (ADR)** is a short, numbered, permanent note of one significant
decision: the situation that forced it, what was decided, what was rejected and why, and what
follows from it. ADRs answer the question a standing reference doc cannot: *why is it this way,
and why didn't we just…?*

This directory is the only place that question gets answered. Plans come and go, reference docs
describe whatever the code does today, and neither keeps a rejected alternative around for long.

## Where each kind of document lives

| Kind | Answers | Home | Lifecycle |
|---|---|---|---|
| **Plan** | What are we about to do, and in what order? | A GitHub issue (preferred), or a `docs/*-plan.md` while its work is open | Temporary. Deleted when its work lands, **after** its decisions have been extracted here |
| **ADR** | Why is it this way, and what was rejected? | `docs/adr/NNNN-*.md` | Permanent. Never deleted; after acceptance only its Status changes |
| **Reference** | How does it work, or how do I do X, today? | `docs/*.md`, `CONTRIBUTING.md`, code docstrings | Living. Edited in the same PR as the behavior. Links here for the *why* instead of retelling it |

## When a decision needs an ADR

Write one when a decision meets **any** of these:

- it is expensive or disruptive to reverse (a packaging format, a wire protocol, a data model);
- it sets or moves a trust or security boundary, or decides who holds a credential;
- it changes how the server is built, released or distributed;
- it rejects an alternative a reasonable newcomer would propose, for a reason that isn't obvious;
- it deliberately departs from a rule written elsewhere in this repo (e.g. "stdlib first");
- someone will plausibly ask "why didn't we just…?" in six months.

Implementation detail, naming, and task sequencing don't need one. If in doubt, write a short one:
a 40-line ADR is fine.

## Rules

1. **One decision per ADR.** A document that decides several things is a plan; split its decisions
   out.
2. **Frozen once accepted.** After `Accepted`, the body is not rewritten. The only permitted edits
   are the Status section, fixing a broken link, and typos. Implementation progress, rollout state
   and "phase N landed" notes belong in the tracking issue, not here.
3. **Change your mind with a new ADR.** A reversal or significant amendment is a new ADR that says
   `Supersedes NNNN` (or `Amends NNNN`); the old one's Status gains one line pointing forward.
   This holds even when a plan says "amend ADR NNNN in place": the plan is wrong about it.
4. **Link only to things that last.** Issues, PRs, commit SHAs, source files, and other ADRs. Never
   link a plan document: it will be deleted. To cite a plan that is already gone, give the commit
   that deleted it and the path, e.g. `git show abc1234^:docs/some-feature-plan.md`.
5. **Retiring a plan is gated on this directory.** The PR that deletes a plan either adds or amends
   the ADRs for every decision the plan contained, or states in its description that the plan
   contained none. This is part of the definition of done
   ([`coding-and-testing-guidelines.md`, "Definition of done"](../coding-and-testing-guidelines.md#definition-of-done)).
6. **Numbers are never reused.** Take the next free number when you open the PR; if a parallel
   branch takes it first, renumber yours before merging.
7. **Update the index below** in the same PR, and `docs/README.md` needs no change: it links here.

### Status values

| Status | Meaning |
|---|---|
| `Proposed` | Under discussion; decides nothing yet |
| `Accepted` | In force. Say whether it is implemented if that isn't obvious |
| `Rejected` | Considered and declined; kept so it isn't re-proposed from scratch |
| `Superseded by NNNN` | Replaced; kept for the reasoning trail |
| `Deprecated` | No longer relevant (the thing it governed is gone) with no replacement |

A decision recorded after the fact says so: `Accepted (recorded retroactively on YYYY-MM-DD;
decided around YYYY-MM-DD in <source>)`.

## Template

Copy this into `docs/adr/NNNN-short-kebab-title.md`. The title states the decision, not the topic.

```markdown
# ADR NNNN: <the decision, stated as a short sentence>

## Status

Accepted — YYYY-MM-DD. <One line on implementation state if not obvious.>
<Supersedes / Amends / Superseded by lines, one each, if any.>

## Context

What forced a decision: the problem, the constraints, the facts that mattered. Short.

## Decision

What was decided, stated so it can be checked against the code.

## Alternatives considered

- **<Alternative>** — why it was rejected.

## Consequences

What becomes easier, what becomes harder, what is now ruled out, what risk is accepted.

## Verification

Where the decision is enforced or observable: files, tests, workflows, guards.

## Related

Issues, PRs, commits, other ADRs. For a deleted source document: `git show <sha>^:<path>`.
```

## Index

Each ADR applies until a later one supersedes it.

| # | Decision | Status |
|---|---|---|
| [0001](0001-record-decisions-as-adrs-and-keep-plans-temporary.md) | Record decisions as ADRs; plans are temporary and deleted when their work lands | Accepted |
| [0002](0002-process-docs-live-in-docs-not-in-claude-md.md) | Process documentation lives in `docs/` and `CONTRIBUTING.md`, not in `CLAUDE.md` | Accepted |
| [0003](0003-use-the-official-mcp-sdk.md) | Use the official MCP Python SDK, not a hand-rolled transport | Accepted |
| [0004](0004-the-version-comes-from-the-git-tag.md) | The version comes from the git tag; no version string in the source tree | Accepted |
| [0005](0005-changelog-is-the-only-source-of-release-notes.md) | `CHANGELOG.md` is the only source of release notes | Accepted |
| [0006](0006-a-release-is-cut-by-a-workflow-after-a-build-pre-flight.md) | A release tag is pushed by a workflow with a non-`GITHUB_TOKEN` credential, after a `build.yml` pre-flight | Accepted |
| [0007](0007-live-upstream-credentials-only-on-a-self-hosted-runner.md) | Live upstream credentials exist only on a self-hosted runner | Accepted |
| [0008](0008-code-and-docs-carry-no-project-history.md) | Code and standing docs carry no project history | Accepted |
| [0009](0009-pypi-publishing-uses-trusted-publishing-only.md) | PyPI publishing uses OIDC trusted publishing only | Accepted |
| [0010](0010-the-server-runs-on-headless-linux-only.md) | The server runs on headless Linux only; CI tests no other OS | Accepted |
| [0011](a-standalone-server-that-writes-into-the-web-servers-folder.md) | A standalone server that writes pages into the web server's folder; it never serves them | Accepted |
| [0012](pages-are-served-from-a-separate-sandboxed-host-name.md) | Pages are served from a separate, sandboxed host name | Accepted |
| [0013](a-page-link-is-only-a-random-id-and-ownership-is-an-index.md) | A page's link is only a random id; ownership is an index | Accepted |
| [0014](the-server-is-its-own-oauth-server-and-google-only-proves-identity.md) | The server is its own OAuth server; Google only proves identity | Accepted |
| [0015](client-registration-accepts-only-allowlisted-redirect-uris.md) | Client registration accepts only allowlisted redirect URIs | Accepted |
| [0016](google-id-tokens-are-trusted-by-tls-not-by-signature.md) | Google ID tokens are trusted by TLS, not by signature | Accepted |
| [0017](all-state-is-one-sqlite-file.md) | All state is one SQLite file | Accepted |
| [0018](large-pages-arrive-through-one-time-upload-urls.md) | Large pages arrive through one-time upload URLs | Accepted |
| [0019](streamable-http-is-stateless-and-starlette-and-uvicorn-are-declared.md) | Streamable HTTP is stateless; Starlette and uvicorn are declared | Accepted |
| [0020](pages-are-fetched-only-from-allowlisted-https-hosts.md) | Pages are fetched only from allowlisted https hosts | Accepted |
| [0021](every-page-expires-and-its-owner-can-extend-it.md) | Every page expires, and its owner can extend it | Accepted |
