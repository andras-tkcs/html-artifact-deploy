"""html_artifact_deploy.page_index: ownership, expiry and ordering."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from html_artifact_deploy.page_index import PageIdTakenError, PageIndex, PageRecord
from html_artifact_deploy.state import DB_FILE_NAME, StateDB

pytestmark = pytest.mark.unit


class Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _record(page_id: str = "p1", owner: str = "a@x.org", *, updated: int = 100, expires: int = 5000) -> PageRecord:
    return PageRecord(page_id, owner, f"title {page_id}", 10, "h" * 64, 50, updated, expires)


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def index(tmp_path: Path, clock: Clock) -> PageIndex:
    return PageIndex(StateDB(tmp_path / DB_FILE_NAME), clock=clock)


class TestRoundTrip:
    def test_insert_then_get(self, index: PageIndex) -> None:
        record = _record()
        index.insert(record)
        assert index.get(record.owner, record.page_id) == record
        assert index.exists(record.page_id)

    def test_get_with_another_owner_is_none(self, index: PageIndex) -> None:
        index.insert(_record())
        assert index.get("b@x.org", "p1") is None
        assert index.get_any("b@x.org", "p1") is None

    def test_get_of_an_absent_page_is_none(self, index: PageIndex) -> None:
        assert index.get("a@x.org", "nope") is None
        assert not index.exists("nope")

    def test_insert_of_an_existing_id_raises(self, index: PageIndex) -> None:
        index.insert(_record())
        with pytest.raises(PageIdTakenError):
            index.insert(_record(owner="b@x.org"))

    def test_clock_is_public(self, index: PageIndex, clock: Clock) -> None:
        assert index.clock is clock


class TestList:
    def test_orders_newest_first_then_page_id_and_limits(self, index: PageIndex) -> None:
        index.insert(_record("b", updated=200))
        index.insert(_record("a", updated=200))
        index.insert(_record("c", updated=300))
        index.insert(_record("old", updated=10))
        assert [r.page_id for r in index.list("a@x.org", 10)] == ["c", "a", "b", "old"]
        assert [r.page_id for r in index.list("a@x.org", 2)] == ["c", "a"]

    def test_excludes_other_owners_and_expired(self, index: PageIndex) -> None:
        index.insert(_record("mine"))
        index.insert(_record("theirs", owner="b@x.org"))
        index.insert(_record("gone", expires=1000))
        assert [r.page_id for r in index.list("a@x.org", 10)] == ["mine"]


class TestUpdateLive:
    def test_changes_a_live_row_and_keeps_created_at(self, index: PageIndex) -> None:
        index.insert(_record())
        changed = replace(
            _record(), title="new", bytes=99, sha256="z" * 64, updated_at=500, expires_at=9000, created_at=999
        )
        assert index.update_live(changed)
        got = index.get("a@x.org", "p1")
        assert got == replace(changed, created_at=50)

    def test_refuses_another_owner(self, index: PageIndex) -> None:
        index.insert(_record())
        assert not index.update_live(replace(_record(), owner="b@x.org", title="new"))
        assert index.get("a@x.org", "p1") == _record()

    def test_refuses_an_expired_row(self, index: PageIndex) -> None:
        index.insert(_record(expires=1000))
        assert not index.update_live(replace(_record(), title="new", expires_at=9000))
        assert index.get_any("a@x.org", "p1") == _record(expires=1000)


class TestSetExpiry:
    def test_changes_a_live_row(self, index: PageIndex) -> None:
        index.insert(_record())
        assert index.set_expiry("a@x.org", "p1", 7777)
        got = index.get("a@x.org", "p1")
        assert got is not None and got.expires_at == 7777

    def test_refuses_another_owner(self, index: PageIndex) -> None:
        index.insert(_record())
        assert not index.set_expiry("b@x.org", "p1", 7777)

    def test_refuses_an_expired_row(self, index: PageIndex) -> None:
        index.insert(_record(expires=1000))
        assert not index.set_expiry("a@x.org", "p1", 9999)
        got = index.get_any("a@x.org", "p1")
        assert got is not None and got.expires_at == 1000


class TestExpiry:
    def test_expired_row_is_invisible_except_to_get_any_exists_and_expired(self, index: PageIndex) -> None:
        index.insert(_record(expires=1000))
        assert index.get("a@x.org", "p1") is None
        assert index.list("a@x.org", 10) == []
        assert not index.set_expiry("a@x.org", "p1", 9999)
        assert index.get_any("a@x.org", "p1") is not None
        assert index.exists("p1")
        assert [r.page_id for r in index.expired(10)] == ["p1"]

    def test_expired_respects_limit_and_order(self, index: PageIndex) -> None:
        index.insert(_record("late", expires=900))
        index.insert(_record("early", expires=100))
        index.insert(_record("mid", expires=500))
        index.insert(_record("live", expires=5000))
        assert [r.page_id for r in index.expired(10)] == ["early", "mid", "late"]
        assert [r.page_id for r in index.expired(2)] == ["early", "mid"]

    def test_remove_expired_deletes_only_an_expired_row(self, index: PageIndex) -> None:
        index.insert(_record("dead", expires=1000))
        index.insert(_record("live"))
        assert index.remove_expired("dead")
        assert not index.exists("dead")
        assert not index.remove_expired("live")
        assert index.exists("live")

    def test_requeue_expired_moves_a_row_behind_the_others(self, index: PageIndex, clock: Clock) -> None:
        index.insert(_record("first", expires=100))
        index.insert(_record("second", expires=200))
        clock.now = 1500
        index.requeue_expired("first")
        assert [r.page_id for r in index.expired(10)] == ["second", "first"]
        got = index.get_any("a@x.org", "first")
        assert got is not None and got.expires_at == 1500

    def test_requeue_expired_leaves_a_live_row(self, index: PageIndex) -> None:
        index.insert(_record())
        index.requeue_expired("p1")
        assert index.get("a@x.org", "p1") == _record()


class TestRemove:
    def test_returns_true_then_false(self, index: PageIndex) -> None:
        index.insert(_record())
        assert index.remove("a@x.org", "p1")
        assert not index.remove("a@x.org", "p1")

    def test_another_owner_leaves_the_row(self, index: PageIndex) -> None:
        index.insert(_record())
        assert not index.remove("b@x.org", "p1")
        assert index.exists("p1")

    def test_works_on_an_expired_row(self, index: PageIndex) -> None:
        index.insert(_record(expires=1000))
        assert index.remove("a@x.org", "p1")
        assert not index.exists("p1")
