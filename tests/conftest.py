"""Shared fixtures, and the one place module-level state is reset between tests.

Any module-level state added under `src/html_artifact_deploy/` (a cache, a singleton, a client) gets a
matching line in `_reset()` in the same PR, or it leaks between tests silently
(docs/coding-and-testing-guidelines.md, "Fixtures and isolation").
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest


def _reset() -> None:
    """Put every piece of module-level state back to its import-time value."""


@pytest.fixture(autouse=True)
def _reset_singletons() -> Iterator[None]:
    _reset()
    yield
    _reset()
