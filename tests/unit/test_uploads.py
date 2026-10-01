"""html_artifact_deploy.uploads: one-time upload slots, driven over the real PUT route of the HTTP app."""

from __future__ import annotations

import os
import sqlite3
import stat
from collections.abc import AsyncIterator, Iterator
from contextlib import asynccontextmanager
from pathlib import Path

import httpx2
import pytest

from html_artifact_deploy.config import AuthConfig, Config, HttpConfig, PagesConfig
from html_artifact_deploy.http_app import build_http_app
from html_artifact_deploy.state import DB_FILE_NAME, StateDB, secret_hash
from html_artifact_deploy.uploads import MAX_OPEN_UPLOADS, UPLOAD_TTL, UploadError, UploadStore

pytestmark = pytest.mark.unit

PUBLIC_URL = "https://publish.example.com"
ALICE = "alice@example.com"
BOB = "bob@example.com"
NOW = 1_800_000_000.0
MAX_BYTES = 1000
NOT_FOUND = "Unknown or expired upload."
NO_UPLOAD = (
    "No uploaded file for that upload_id. "
    "Call create_page_upload, PUT the file to its upload_url, then call publish_page again."
)


class Clock:
    def __init__(self) -> None:
        self.now = NOW

    def __call__(self) -> float:
        return self.now


class StubGoogle:
    def authorization_url(self, state: str, nonce: str, redirect_uri: str) -> str:  # pragma: no cover
        raise AssertionError("not used")

    async def exchange_code(self, code: str, redirect_uri: str, nonce: str) -> object:  # pragma: no cover
        raise AssertionError("not used")


class Env:
    def __init__(self, tmp_path: Path) -> None:
        pages = tmp_path / "pages"
        pages.mkdir()
        self.clock = Clock()
        config = Config(
            path=tmp_path / "config.toml",
            pages=PagesConfig(root=pages, public_base_url="https://pages.example.com", max_bytes=MAX_BYTES),
            state_dir=tmp_path / "state",
            http=HttpConfig(public_url=PUBLIC_URL),
            auth=AuthConfig(
                google_client_id="x.apps.googleusercontent.com", allowed_domains=("example.com",), allowed_emails=()
            ),
        )
        self.app = build_http_app(
            config,
            environ={"HTML_ARTIFACT_DEPLOY_GOOGLE_CLIENT_SECRET": "secret"},
            google=StubGoogle(),  # type: ignore[arg-type]
            clock=self.clock,
        )
        self.folder = tmp_path / "state" / "uploads"
        self.db_path = tmp_path / "state" / DB_FILE_NAME
        self.db = StateDB(self.db_path)
        self.store = UploadStore(self.db, self.folder, PUBLIC_URL, MAX_BYTES, clock=self.clock)

    @asynccontextmanager
    async def client(self) -> AsyncIterator[httpx2.AsyncClient]:
        async with self.app.router.lifespan_context(self.app):
            transport = httpx2.ASGITransport(app=self.app)
            async with httpx2.AsyncClient(transport=transport, base_url=PUBLIC_URL) as client:
                yield client

    def files(self) -> list[str]:
        return sorted(path.name for path in self.folder.iterdir())

    def rows(self) -> list[tuple[str, str]]:
        connection = sqlite3.connect(self.db_path)
        try:
            return list(connection.execute("SELECT owner, state FROM uploads"))
        finally:
            connection.close()


@pytest.fixture
def env(tmp_path: Path) -> Iterator[Env]:
    environment = Env(tmp_path)
    yield environment
    environment.db.close()


class TestRoundTrip:
    async def test_create_put_read_discard(self, env: Env) -> None:
        slot = await env.store.create(ALICE)
        assert slot.upload_url == f"{PUBLIC_URL}/uploads/{slot.upload_id}"
        assert (slot.max_bytes, slot.expires_at) == (MAX_BYTES, int(NOW) + UPLOAD_TTL)
        async with env.client() as client:
            response = await client.put(f"/uploads/{slot.upload_id}", content=b"<p>hi</p>")
        assert (response.status_code, response.text) == (
            201,
            "Uploaded 9 bytes. Now call publish_page with this upload_id.",
        )
        assert env.files() == [secret_hash(slot.upload_id)]
        assert await env.store.read(ALICE, slot.upload_id) == b"<p>hi</p>"
        await env.store.discard(slot.upload_id)
        assert env.files() == []
        assert env.rows() == []

    async def test_folder_is_private(self, env: Env) -> None:
        assert stat.S_IMODE(os.stat(env.folder).st_mode) == 0o700


class TestPut:
    async def test_unknown_id_is_404(self, env: Env) -> None:
        async with env.client() as client:
            response = await client.put("/uploads/nope", content=b"x")
        assert (response.status_code, response.text) == (404, NOT_FOUND)

    async def test_filled_slot_is_404(self, env: Env) -> None:
        slot = await env.store.create(ALICE)
        async with env.client() as client:
            first = await client.put(f"/uploads/{slot.upload_id}", content=b"one")
            second = await client.put(f"/uploads/{slot.upload_id}", content=b"two")
        assert (first.status_code, second.status_code, second.text) == (201, 404, NOT_FOUND)
        assert await env.store.read(ALICE, slot.upload_id) == b"one"

    async def test_expired_slot_is_404(self, env: Env) -> None:
        slot = await env.store.create(ALICE)
        env.clock.now += UPLOAD_TTL
        async with env.client() as client:
            response = await client.put(f"/uploads/{slot.upload_id}", content=b"x")
        assert (response.status_code, response.text) == (404, NOT_FOUND)
        assert env.files() == []

    async def test_filled_slot_that_expires_is_not_readable(self, env: Env) -> None:
        slot = await env.store.create(ALICE)
        async with env.client() as client:
            await client.put(f"/uploads/{slot.upload_id}", content=b"x")
        env.clock.now += UPLOAD_TTL
        with pytest.raises(UploadError, match="No uploaded file"):
            await env.store.read(ALICE, slot.upload_id)

    async def test_declared_length_over_the_limit_is_413(self, env: Env) -> None:
        slot = await env.store.create(ALICE)
        async with env.client() as client:
            response = await client.put(f"/uploads/{slot.upload_id}", content=b"x" * (MAX_BYTES + 1))
        assert (response.status_code, response.text) == (413, f"The file is over the {MAX_BYTES:,}-byte limit.")
        assert env.files() == []

    async def test_streamed_body_over_the_limit_is_413_and_leaves_no_file(self, env: Env) -> None:
        slot = await env.store.create(ALICE)
        consumed = 0

        async def body() -> AsyncIterator[bytes]:
            nonlocal consumed
            for _ in range(100):
                consumed += 1
                yield b"x" * 100

        async with env.client() as client:
            response = await client.put(f"/uploads/{slot.upload_id}", content=body())
        assert response.status_code == 413
        assert consumed < 100  # the body was not read to the end
        assert env.files() == []
        assert env.rows() == [(ALICE, "open")]

    async def test_body_at_the_limit_is_accepted(self, env: Env) -> None:
        slot = await env.store.create(ALICE)
        async with env.client() as client:
            response = await client.put(f"/uploads/{slot.upload_id}", content=b"x" * MAX_BYTES)
        assert response.status_code == 201

    async def test_empty_body_is_400(self, env: Env) -> None:
        slot = await env.store.create(ALICE)
        async with env.client() as client:
            response = await client.put(f"/uploads/{slot.upload_id}", content=b"")
        assert (response.status_code, response.text) == (400, "The upload is empty.")
        assert env.files() == []
        assert env.rows() == [(ALICE, "open")]

    async def test_losing_side_of_a_concurrent_fill_is_404_and_leaves_no_file(
        self, env: Env, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        slot = await env.store.create(ALICE)
        original = UploadStore._mark_filled

        def other_put_wins(self: UploadStore, upload_id: str, size: int) -> bool:
            assert original(self, upload_id, 1)
            return original(self, upload_id, size)

        monkeypatch.setattr(UploadStore, "_mark_filled", other_put_wins)
        async with env.client() as client:
            response = await client.put(f"/uploads/{slot.upload_id}", content=b"late")
        assert (response.status_code, response.text) == (404, NOT_FOUND)
        assert env.files() == []


class TestRead:
    async def test_another_owner_is_refused(self, env: Env) -> None:
        slot = await env.store.create(ALICE)
        async with env.client() as client:
            await client.put(f"/uploads/{slot.upload_id}", content=b"x")
        with pytest.raises(UploadError) as error:
            await env.store.read(BOB, slot.upload_id)
        assert str(error.value) == NO_UPLOAD

    async def test_open_slot_is_not_readable(self, env: Env) -> None:
        slot = await env.store.create(ALICE)
        with pytest.raises(UploadError, match="No uploaded file"):
            await env.store.read(ALICE, slot.upload_id)

    async def test_missing_file_is_not_readable(self, env: Env) -> None:
        slot = await env.store.create(ALICE)
        async with env.client() as client:
            await client.put(f"/uploads/{slot.upload_id}", content=b"x")
        (env.folder / secret_hash(slot.upload_id)).unlink()
        with pytest.raises(UploadError, match="No uploaded file"):
            await env.store.read(ALICE, slot.upload_id)


class TestCreate:
    async def test_the_eleventh_open_slot_is_refused(self, env: Env) -> None:
        for _ in range(MAX_OPEN_UPLOADS):
            await env.store.create(ALICE)
        with pytest.raises(UploadError) as error:
            await env.store.create(ALICE)
        assert str(error.value) == (
            f"You have {MAX_OPEN_UPLOADS} uploads waiting. Publish them, or wait 15 minutes for them to expire."
        )
        await env.store.create(BOB)
        assert len(env.rows()) == MAX_OPEN_UPLOADS + 1

    async def test_purges_expired_rows_files_and_stale_part_files(self, env: Env) -> None:
        slot = await env.store.create(ALICE)
        async with env.client() as client:
            await client.put(f"/uploads/{slot.upload_id}", content=b"x")
        stale = env.folder / "old.part"
        fresh = env.folder / "new.part"
        stale.write_bytes(b"x")
        fresh.write_bytes(b"x")
        os.utime(stale, (NOW - UPLOAD_TTL - 1, NOW - UPLOAD_TTL - 1))
        os.utime(fresh, (NOW, NOW))
        env.clock.now += UPLOAD_TTL
        os.utime(fresh, (env.clock.now, env.clock.now))
        new = await env.store.create(BOB)
        assert env.rows() == [(BOB, "open")]
        assert env.files() == ["new.part"]
        assert new.expires_at == int(env.clock.now) + UPLOAD_TTL
