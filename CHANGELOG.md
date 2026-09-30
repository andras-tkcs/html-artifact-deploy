# Changelog

<!--
HOW TO USE THIS FILE

1. `## [Unreleased]` is permanent. A feature branch adds its user-visible change under that
   heading and nothing else. Do NOT open a concrete `## [X.Y.Z]` heading on a feature branch: two
   branches in flight would both claim the same next version. Only the PR that prepares a release
   turns `## [Unreleased]` into `## [X.Y.Z] — YYYY-MM-DD`, adds a fresh empty `## [Unreleased]`
   above it, and updates the link definitions at the bottom. If a section for that version already
   exists, MERGE `[Unreleased]`'s entries into it and fix its date; renaming would create a second
   heading, and scripts/changelog_section.py refuses to render a version that has two.

2. This file is NEVER a version source. The git tag, through setuptools_scm, is the only one
   (docs/releasing.md). scripts/changelog_section.py reads a version's section *out* of this file
   to produce the GitHub Release body for that tag.

3. Pre-release tags (aN/bN/rcN) get no section of their own; their content folds into the version
   they lead to, per Keep a Changelog.

4. A version that was tagged but never published gets no section either. Fold its entries into the
   version that does ship and say so at the top of that section.

5. Write entries for the people who use the server: what they can now do, or what behaves
   differently, in plain words. Internal refactors and test-only changes need no entry.
-->

All notable changes to HTML Artifact Deploy are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project
adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- The first MCP server skeleton: `echo` and `server_info` over stdio.

[Unreleased]: https://github.com/andras-tkcs/html-artifact-deploy/commits/main
