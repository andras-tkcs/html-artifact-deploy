"""html_artifact_deploy.service: the publish, list and unpublish rules, step by step."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from html_artifact_deploy.config import PagesConfig
from html_artifact_deploy.page_index import PageIdTakenError, PageIndex, PageRecord
from html_artifact_deploy.pages import is_page_id, page_url
from html_artifact_deploy.service import PageError, PageService
from html_artifact_deploy.state import DB_FILE_NAME, StateDB
from html_artifact_deploy.storage import LocalFolderStore, PageExistsError, StorageError

pytestmark = pytest.mark.unit

ALICE = "alice@example.com"
BOB = "bob@example.com"
BASE = "https://pages.example.com"
DAY = 86400
SAVE_FAILED = (
    "The page could not be saved on the server. Try again later, or ask whoever runs "
    "html-artifact-deploy to check its log."
)
REMOVE_FAILED = (
    "The page could not be removed from the server. Try again later, or ask whoever runs "
    "html-artifact-deploy to check its log."
)
ALLOCATE = "Could not allocate a new page id. Try again."


def no_such_page(page_id: str) -> str:
    return (
        f"No page with page_id {page_id!r} was published by you, or it has expired. Call list_pages to see your pages."
    )


class Clock:
    def __init__(self, now: float = 1_000_000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


@pytest.fixture
def clock() -> Clock:
    return Clock()


@pytest.fixture
def root(tmp_path: Path) -> Path:
    folder = tmp_path / "pages"
    folder.mkdir()
    return folder


@pytest.fixture
def config(root: Path) -> PagesConfig:
    return PagesConfig(root=root, public_base_url=BASE, max_bytes=1000, default_expiry_days=90)


@pytest.fixture
def index(tmp_path: Path, clock: Clock) -> PageIndex:
    return PageIndex(StateDB(tmp_path / "state" / DB_FILE_NAME), clock=clock)


@pytest.fixture
def service(config: PagesConfig, root: Path, index: PageIndex) -> PageService:
    return PageService(config, LocalFolderStore(root), index)


class FakeStore:
    """A PageStore whose operations can be made to fail or to run a hook."""

    def __init__(self) -> None:
        self.write_error: Exception | None = None
        self.delete_error: Exception | None = None
        self.on_write = lambda: None
        self.deleted: list[str] = []

    def check(self) -> None:  # pragma: no cover - never called by the service
        pass

    def write_page(self, page_id: str, data: bytes, *, new: bool) -> None:
        self.on_write()
        if self.write_error is not None:
            raise self.write_error

    def delete_page(self, page_id: str) -> bool:
        self.deleted.append(page_id)
        if self.delete_error is not None:
            raise self.delete_error
        return True


@pytest.fixture
def fake_store() -> FakeStore:
    return FakeStore()


@pytest.fixture
def fake_service(config: PagesConfig, fake_store: FakeStore, index: PageIndex) -> PageService:
    return PageService(config, fake_store, index)


HTML = b"<!doctype html><title>x</title>"


class TestValidation:
    async def test_bad_title(self, service: PageService) -> None:
        with pytest.raises(PageError) as error:
            await service.publish(ALICE, "   ", HTML)
        assert str(error.value) == "title must be 1 to 200 characters."

    async def test_title_too_long(self, service: PageService) -> None:
        with pytest.raises(PageError, match="title must be 1 to 200 characters."):
            await service.publish(ALICE, "t" * 201, HTML)

    async def test_empty_page(self, service: PageService) -> None:
        with pytest.raises(PageError) as error:
            await service.publish(ALICE, "t", b"")
        assert str(error.value) == "The page is empty."

    async def test_page_over_the_limit(self, service: PageService) -> None:
        with pytest.raises(PageError) as error:
            await service.publish(ALICE, "t", b"x" * 1001)
        assert str(error.value) == "The page is 1,001 bytes, over the 1,000-byte limit."

    async def test_page_at_the_limit_is_accepted(self, service: PageService) -> None:
        result = await service.publish(ALICE, "t", b"x" * 1000)
        assert result.record.bytes == 1000

    async def test_not_utf8(self, service: PageService) -> None:
        with pytest.raises(PageError) as error:
            await service.publish(ALICE, "t", b"\xff\xfe")
        assert str(error.value) == "The page is not UTF-8 text, so it is not an HTML page."

    async def test_unknown_page_id(self, service: PageService) -> None:
        page_id = "a" * 32
        with pytest.raises(PageError) as error:
            await service.publish(ALICE, "t", HTML, page_id)
        assert str(error.value) == no_such_page(page_id)


class TestNewPage:
    async def test_writes_the_file_and_records_it(
        self, service: PageService, index: PageIndex, root: Path, clock: Clock
    ) -> None:
        result = await service.publish(ALICE, "  Report ", HTML)
        record = result.record
        assert result.created is True
        assert is_page_id(record.page_id)
        assert result.url == page_url(BASE, record.page_id)
        assert service.url_for(record) == result.url
        assert (root / record.page_id / "index.html").read_bytes() == HTML
        assert record.title == "Report"
        assert record.owner == ALICE
        assert record.bytes == len(HTML)
        assert record.created_at == record.updated_at == int(clock.now)
        assert record.expires_at == int(clock.now) + 90 * DAY
        assert index.get(ALICE, record.page_id) == record

    async def test_a_drawn_id_already_in_the_index_is_skipped(
        self, service: PageService, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        first = (await service.publish(ALICE, "one", HTML)).record.page_id
        ids = iter([first, "b" * 32])
        monkeypatch.setattr("html_artifact_deploy.service.new_page_id", lambda: next(ids))
        second = await service.publish(ALICE, "two", HTML)
        assert second.record.page_id == "b" * 32

    async def test_store_clash_three_times_gives_up(
        self, fake_service: PageService, fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        fake_store.write_error = PageExistsError("taken")
        monkeypatch.setattr("html_artifact_deploy.service.new_page_id", lambda: "c" * 32)
        with pytest.raises(PageError) as error:
            await fake_service.publish(ALICE, "t", HTML)
        assert str(error.value) == ALLOCATE

    async def test_store_clash_then_success(
        self, fake_service: PageService, fake_store: FakeStore, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        ids = iter(["c" * 32, "d" * 32])
        monkeypatch.setattr("html_artifact_deploy.service.new_page_id", lambda: next(ids))
        written: list[str] = []

        def write_page(page_id: str, data: bytes, *, new: bool) -> None:
            if not written and page_id == "c" * 32:
                written.append("clash")
                raise PageExistsError("taken")

        monkeypatch.setattr(fake_store, "write_page", write_page)
        result = await fake_service.publish(ALICE, "t", HTML)
        assert result.record.page_id == "d" * 32

    async def test_insert_race_removes_the_new_folder(
        self, fake_service: PageService, fake_store: FakeStore, index: PageIndex, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def taken(record: PageRecord) -> None:
            raise PageIdTakenError(record.page_id)

        monkeypatch.setattr(index, "insert", taken)
        with pytest.raises(PageError) as error:
            await fake_service.publish(ALICE, "t", HTML)
        assert str(error.value) == ALLOCATE
        assert len(fake_store.deleted) == 1

    async def test_insert_race_cleanup_failure_still_reports_allocation(
        self, fake_service: PageService, fake_store: FakeStore, index: PageIndex, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def taken(record: PageRecord) -> None:
            raise PageIdTakenError(record.page_id)

        monkeypatch.setattr(index, "insert", taken)
        fake_store.delete_error = StorageError("nope")
        with pytest.raises(PageError) as error:
            await fake_service.publish(ALICE, "t", HTML)
        assert str(error.value) == ALLOCATE

    async def test_storage_error_is_mapped(self, fake_service: PageService, fake_store: FakeStore) -> None:
        fake_store.write_error = StorageError("disk full")
        with pytest.raises(PageError) as error:
            await fake_service.publish(ALICE, "t", HTML)
        assert str(error.value) == SAVE_FAILED


class TestReplace:
    async def test_keeps_created_at_expiry_and_url(
        self, service: PageService, index: PageIndex, root: Path, clock: Clock
    ) -> None:
        first = await service.publish(ALICE, "old", HTML)
        clock.now += 5 * DAY
        second = await service.publish(ALICE, "new", b"<p>v2</p>", first.record.page_id)
        assert second.created is False
        assert second.url == first.url
        assert second.record.created_at == first.record.created_at
        assert second.record.expires_at == first.record.expires_at
        assert second.record.updated_at == int(clock.now)
        assert second.record.title == "new"
        assert (root / first.record.page_id / "index.html").read_bytes() == b"<p>v2</p>"
        assert index.get(ALICE, first.record.page_id) == second.record

    async def test_expired_page_cannot_be_replaced(self, service: PageService, clock: Clock) -> None:
        first = await service.publish(ALICE, "old", HTML)
        clock.now += 91 * DAY
        with pytest.raises(PageError) as error:
            await service.publish(ALICE, "new", HTML, first.record.page_id)
        assert str(error.value) == no_such_page(first.record.page_id)

    async def test_another_owner_cannot_replace(self, service: PageService) -> None:
        first = await service.publish(ALICE, "old", HTML)
        with pytest.raises(PageError) as error:
            await service.publish(BOB, "new", HTML, first.record.page_id)
        assert str(error.value) == no_such_page(first.record.page_id)

    async def test_row_expiring_during_the_write(
        self, fake_service: PageService, fake_store: FakeStore, clock: Clock
    ) -> None:
        created = await fake_service.publish(ALICE, "t", HTML)
        fake_store.on_write = lambda: setattr(clock, "now", clock.now + 91 * DAY)
        with pytest.raises(PageError) as error:
            await fake_service.publish(ALICE, "t", HTML, created.record.page_id)
        assert str(error.value) == no_such_page(created.record.page_id)

    async def test_storage_error_is_mapped(self, fake_service: PageService, fake_store: FakeStore) -> None:
        created = await fake_service.publish(ALICE, "t", HTML)
        fake_store.write_error = StorageError("gone")
        with pytest.raises(PageError) as error:
            await fake_service.publish(ALICE, "t", HTML, created.record.page_id)
        assert str(error.value) == SAVE_FAILED


class TestCheckCanPublish:
    async def test_accepts_a_good_title_and_no_page_id(self, service: PageService) -> None:
        await service.check_can_publish(ALICE, "t", "")

    async def test_accepts_an_owned_page(self, service: PageService) -> None:
        page = await service.publish(ALICE, "t", HTML)
        await service.check_can_publish(ALICE, "t", page.record.page_id)

    async def test_bad_title(self, service: PageService) -> None:
        with pytest.raises(PageError, match="title must be 1 to 200 characters."):
            await service.check_can_publish(ALICE, "", "")

    async def test_unowned_page(self, service: PageService) -> None:
        page = await service.publish(ALICE, "t", HTML)
        with pytest.raises(PageError) as error:
            await service.check_can_publish(BOB, "t", page.record.page_id)
        assert str(error.value) == no_such_page(page.record.page_id)


class TestListPages:
    async def test_lists_only_own_pages_newest_first(self, service: PageService, clock: Clock) -> None:
        a = await service.publish(ALICE, "a", HTML)
        clock.now += 10
        b = await service.publish(ALICE, "b", HTML)
        await service.publish(BOB, "c", HTML)
        records = await service.list_pages(ALICE, 50)
        assert [r.page_id for r in records] == [b.record.page_id, a.record.page_id]

    @pytest.mark.parametrize(("limit", "expected"), [(0, 1), (-5, 1), (500, 200), (3, 3)])
    async def test_limit_is_clamped(
        self, service: PageService, monkeypatch: pytest.MonkeyPatch, limit: int, expected: int
    ) -> None:
        seen: list[int] = []

        def fake_list(owner: str, limit: int) -> list[PageRecord]:
            seen.append(limit)
            return []

        monkeypatch.setattr(service._index, "list", fake_list)
        await service.list_pages(ALICE, limit)
        assert seen == [expected]


class TestUnpublish:
    async def test_removes_folder_and_row(self, service: PageService, index: PageIndex, root: Path) -> None:
        page = await service.publish(ALICE, "t", HTML)
        removed = await service.unpublish(ALICE, page.record.page_id)
        assert removed == page.record
        assert not (root / page.record.page_id).exists()
        assert index.get_any(ALICE, page.record.page_id) is None

    async def test_expired_page_with_files_still_removed(self, service: PageService, root: Path, clock: Clock) -> None:
        page = await service.publish(ALICE, "t", HTML)
        clock.now += 91 * DAY
        assert (root / page.record.page_id).exists()
        await service.unpublish(ALICE, page.record.page_id)
        assert not (root / page.record.page_id).exists()

    async def test_another_owner_is_refused(self, service: PageService, root: Path) -> None:
        page = await service.publish(ALICE, "t", HTML)
        with pytest.raises(PageError) as error:
            await service.unpublish(BOB, page.record.page_id)
        assert str(error.value) == no_such_page(page.record.page_id)
        assert (root / page.record.page_id).exists()

    async def test_unknown_page(self, service: PageService) -> None:
        with pytest.raises(PageError, match="No page with page_id"):
            await service.unpublish(ALICE, "d" * 32)

    async def test_folder_already_gone_still_succeeds(self, service: PageService, index: PageIndex, root: Path) -> None:
        page = await service.publish(ALICE, "t", HTML)
        LocalFolderStore(root).delete_page(page.record.page_id)
        await service.unpublish(ALICE, page.record.page_id)
        assert index.get_any(ALICE, page.record.page_id) is None

    async def test_storage_error_is_mapped_and_index_unchanged(
        self, fake_service: PageService, fake_store: FakeStore, index: PageIndex
    ) -> None:
        page = await fake_service.publish(ALICE, "t", HTML)
        fake_store.delete_error = StorageError("busy")
        with pytest.raises(PageError) as error:
            await fake_service.unpublish(ALICE, page.record.page_id)
        assert str(error.value) == REMOVE_FAILED
        assert index.get(ALICE, page.record.page_id) == page.record


class TestLogging:
    async def test_log_lines_have_ids_and_owner_not_title(
        self, service: PageService, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.INFO, logger="html_artifact_deploy.service"):
            page = await service.publish(ALICE, "Secret Quarterly Title", HTML)
            await service.unpublish(ALICE, page.record.page_id)
        text = caplog.text
        assert f"published page_id={page.record.page_id} owner={ALICE}" in text
        assert f"unpublished page_id={page.record.page_id} owner={ALICE}" in text
        assert "Secret Quarterly Title" not in text
