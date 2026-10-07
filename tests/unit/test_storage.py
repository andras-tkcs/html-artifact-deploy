"""html_artifact_deploy.storage: LocalFolderStore on a real temporary folder."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from html_artifact_deploy import storage
from html_artifact_deploy.storage import LocalFolderStore, PageExistsError, StorageError

pytestmark = pytest.mark.unit

PAGE_ID = "0123456789abcdef0123456789abcdef"
WRITE_FAILED = "The page could not be saved in the pages folder."
DELETE_FAILED = "The page could not be removed from the pages folder."


def mode(path: Path) -> int:
    return stat.S_IMODE(path.stat().st_mode)


class TestWritePage:
    def test_new_page_is_written_and_readable(self, tmp_path: Path) -> None:
        LocalFolderStore(tmp_path).write_page(PAGE_ID, b"<p>hi</p>", new=True)
        assert (tmp_path / PAGE_ID / "index.html").read_bytes() == b"<p>hi</p>"

    def test_new_on_an_existing_folder_raises_page_exists(self, tmp_path: Path) -> None:
        (tmp_path / PAGE_ID).mkdir()
        with pytest.raises(PageExistsError, match="already exists"):
            LocalFolderStore(tmp_path).write_page(PAGE_ID, b"x", new=True)

    def test_replacement_keeps_one_file_and_no_temp_leftovers(self, tmp_path: Path) -> None:
        store = LocalFolderStore(tmp_path)
        store.write_page(PAGE_ID, b"one", new=True)
        store.write_page(PAGE_ID, b"two", new=False)
        assert [p.name for p in (tmp_path / PAGE_ID).iterdir()] == ["index.html"]
        assert (tmp_path / PAGE_ID / "index.html").read_bytes() == b"two"

    def test_replacing_a_missing_folder_raises(self, tmp_path: Path) -> None:
        with pytest.raises(StorageError, match="The page's folder is missing from the pages folder."):
            LocalFolderStore(tmp_path).write_page(PAGE_ID, b"x", new=False)

    def test_modes_are_world_readable(self, tmp_path: Path) -> None:
        os.chmod(tmp_path, 0o755)
        old_umask = os.umask(0o077)
        try:
            LocalFolderStore(tmp_path).write_page(PAGE_ID, b"x", new=True)
        finally:
            os.umask(old_umask)
        assert mode(tmp_path / PAGE_ID) == 0o755
        assert mode(tmp_path / PAGE_ID / "index.html") == 0o644

    def test_replacement_repairs_the_folder_mode(self, tmp_path: Path) -> None:
        store = LocalFolderStore(tmp_path)
        store.write_page(PAGE_ID, b"x", new=True)
        os.chmod(tmp_path / PAGE_ID, 0o700)
        store.write_page(PAGE_ID, b"y", new=False)
        assert mode(tmp_path / PAGE_ID) == 0o755

    def test_bad_id_is_a_value_error(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            LocalFolderStore(tmp_path).write_page("../x", b"x", new=True)

    def test_symlinked_folder_is_refused(self, tmp_path: Path) -> None:
        target = tmp_path / "elsewhere"
        target.mkdir()
        (tmp_path / PAGE_ID).symlink_to(target)
        with pytest.raises(StorageError, match="contains a link"):
            LocalFolderStore(tmp_path).write_page(PAGE_ID, b"x", new=False)
        assert list(target.iterdir()) == []

    def test_os_replace_failure_removes_the_temp_file(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        store = LocalFolderStore(tmp_path)
        store.write_page(PAGE_ID, b"one", new=True)

        def boom(src: object, dst: object) -> None:
            raise OSError(5, "disk on fire")

        monkeypatch.setattr(storage.os, "replace", boom)
        with pytest.raises(StorageError, match=WRITE_FAILED):
            store.write_page(PAGE_ID, b"two", new=False)
        assert [p.name for p in (tmp_path / PAGE_ID).iterdir()] == ["index.html"]
        assert (tmp_path / PAGE_ID / "index.html").read_bytes() == b"one"

    def test_unlink_failure_does_not_hide_the_storage_error(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        def boom(*args: object) -> None:
            raise OSError(5, "disk on fire")

        monkeypatch.setattr(storage.os, "replace", boom)
        monkeypatch.setattr(storage.os, "unlink", boom)
        with pytest.raises(StorageError, match=WRITE_FAILED):
            LocalFolderStore(tmp_path).write_page(PAGE_ID, b"x", new=True)

    def test_mkstemp_failure_is_a_storage_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        def boom(**kwargs: object) -> None:
            raise OSError(28, "no space")

        monkeypatch.setattr(storage.tempfile, "mkstemp", boom)
        with pytest.raises(StorageError, match=WRITE_FAILED):
            LocalFolderStore(tmp_path).write_page(PAGE_ID, b"x", new=True)

    def test_failure_is_logged_without_the_path(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
    ) -> None:
        def boom(**kwargs: object) -> None:
            raise OSError(28, "no space")

        monkeypatch.setattr(storage.tempfile, "mkstemp", boom)
        with pytest.raises(StorageError):
            LocalFolderStore(tmp_path).write_page(PAGE_ID, b"x", new=True)
        assert f"Pages folder write failed for {PAGE_ID}: no space" in caplog.text


class TestDeletePage:
    def test_returns_true_then_false(self, tmp_path: Path) -> None:
        store = LocalFolderStore(tmp_path)
        store.write_page(PAGE_ID, b"x", new=True)
        assert store.delete_page(PAGE_ID) is True
        assert not (tmp_path / PAGE_ID).exists()
        assert store.delete_page(PAGE_ID) is False

    def test_bad_id_is_a_value_error(self, tmp_path: Path) -> None:
        with pytest.raises(ValueError):
            LocalFolderStore(tmp_path).delete_page("../x")

    def test_symlinked_folder_is_refused(self, tmp_path: Path) -> None:
        target = tmp_path / "elsewhere"
        target.mkdir()
        (tmp_path / PAGE_ID).symlink_to(target)
        with pytest.raises(StorageError, match="contains a link"):
            LocalFolderStore(tmp_path).delete_page(PAGE_ID)
        assert target.is_dir()

    def test_rmtree_failure_is_a_storage_error(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        store = LocalFolderStore(tmp_path)
        store.write_page(PAGE_ID, b"x", new=True)

        def boom(path: object) -> None:
            raise OSError(13, "denied")

        monkeypatch.setattr(storage.shutil, "rmtree", boom)
        with pytest.raises(StorageError, match=DELETE_FAILED):
            store.delete_page(PAGE_ID)


class TestCheck:
    def test_writable_root_passes(self, tmp_path: Path) -> None:
        LocalFolderStore(tmp_path).check()

    def test_missing_root_fails(self, tmp_path: Path) -> None:
        missing = tmp_path / "nope"
        with pytest.raises(StorageError, match=f"The pages folder {missing} does not exist"):
            LocalFolderStore(missing).check()

    @pytest.mark.skipif(os.geteuid() == 0, reason="root can write to a read-only folder")
    def test_read_only_root_fails(self, tmp_path: Path) -> None:
        os.chmod(tmp_path, 0o555)
        try:
            with pytest.raises(StorageError, match="cannot write to it"):
                LocalFolderStore(tmp_path).check()
        finally:
            os.chmod(tmp_path, 0o755)
