"""Tests for xteink_service.covers - cover lookup, download safety, caching.

Every network call is faked. Nothing here touches Open Library.
"""
import hashlib
import io
import json
import sqlite3

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from xteink_service import covers


# ------------------------------------------------------------------ #
# Fake aiohttp session                                                 #
# ------------------------------------------------------------------ #

class _FakeContent:
    def __init__(self, data: bytes, chunk: int = 64 * 1024):
        self._data = data
        self._chunk = chunk

    def iter_chunked(self, _n):
        data, chunk = self._data, self._chunk

        async def gen():
            for i in range(0, len(data), chunk):
                yield data[i:i + chunk]

        return gen()


class _FakeResponse:
    def __init__(self, data: bytes = b"", status: int = 200, headers: dict | None = None):
        self.status = status
        self.headers = headers or {}
        self.content = _FakeContent(data)
        self._data = data

    async def json(self, content_type=None):
        return json.loads(self._data)

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_exc):
        return False


class _FakeSession:
    """Returns queued responses in order and records the URLs asked for."""

    def __init__(self, *responses):
        self._queue = list(responses)
        self.urls: list[str] = []

    def get(self, url, **_kw):
        self.urls.append(url)
        return self._queue.pop(0)


def _search_response(cover_id):
    docs = [{"cover_i": cover_id}] if cover_id else [{}]
    return _FakeResponse(json.dumps({"docs": docs}).encode())


def _png(width: int, height: int) -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (width, height), (120, 90, 60)).save(buf, format="PNG")
    return buf.getvalue()


# ------------------------------------------------------------------ #
# Cache table                                                          #
# ------------------------------------------------------------------ #

def test_init_db_is_idempotent(tmp_path):
    state_db = str(tmp_path / "state.db")
    covers.init_db(state_db)
    covers.init_db(state_db)
    with sqlite3.connect(state_db) as conn:
        cols = [r[1] for r in conn.execute("PRAGMA table_info(book_covers)")]
    assert cols == ["title", "cover_path", "version", "status", "updated_at"]
    assert covers.get_covers(state_db) == {}


# ------------------------------------------------------------------ #
# URL allowlist                                                        #
# ------------------------------------------------------------------ #

def test_allowed_url_accepts_only_the_cover_host():
    assert covers._allowed_url("https://covers.openlibrary.org/b/id/42-L.jpg")
    assert not covers._allowed_url("https://evil.example.com/b/id/42-L.jpg")
    assert not covers._allowed_url("https://covers.openlibrary.org.evil.com/x.jpg")
    assert not covers._allowed_url("https://openlibrary.org/b/id/42-L.jpg")


def test_allowed_url_rejects_non_https():
    assert not covers._allowed_url("http://covers.openlibrary.org/b/id/42-L.jpg")
    assert not covers._allowed_url("file:///etc/passwd")
    assert not covers._allowed_url("ftp://covers.openlibrary.org/42.jpg")


async def test_download_refuses_foreign_host_without_requesting():
    session = _FakeSession()
    out = await covers._download_image(session, "https://evil.example.com/42.jpg")
    assert out is None
    assert session.urls == []


async def test_download_refuses_plain_http_without_requesting():
    session = _FakeSession()
    out = await covers._download_image(session, "http://covers.openlibrary.org/42.jpg")
    assert out is None
    assert session.urls == []


# ------------------------------------------------------------------ #
# Download limits                                                      #
# ------------------------------------------------------------------ #

async def test_download_rejects_oversize_body():
    oversize = b"x" * (covers._MAX_BYTES + 1024)
    session = _FakeSession(_FakeResponse(oversize))
    out = await covers._download_image(
        session, "https://covers.openlibrary.org/b/id/1-L.jpg"
    )
    assert out is None


async def test_download_rejects_oversize_content_length():
    session = _FakeSession(
        _FakeResponse(b"tiny", headers={"Content-Length": str(covers._MAX_BYTES + 1)})
    )
    out = await covers._download_image(
        session, "https://covers.openlibrary.org/b/id/1-L.jpg"
    )
    assert out is None


async def test_download_accepts_a_normal_body():
    png = _png(400, 600)
    session = _FakeSession(_FakeResponse(png))
    out = await covers._download_image(
        session, "https://covers.openlibrary.org/b/id/1-L.jpg"
    )
    assert out == png


# ------------------------------------------------------------------ #
# Image validation                                                     #
# ------------------------------------------------------------------ #

def test_encode_rejects_html_served_as_200():
    html = b"<!DOCTYPE html><html><body>Not found</body></html>"
    assert covers._encode_cover(html) is None


def test_encode_rejects_tiny_placeholder_pixel():
    assert covers._encode_cover(_png(1, 1)) is None
    assert covers._encode_cover(_png(99, 99)) is None


def test_encode_downscales_and_reencodes_to_jpeg():
    out = covers._encode_cover(_png(1200, 1800))
    assert out is not None
    img = Image.open(io.BytesIO(out))
    assert img.format == "JPEG"
    assert max(img.size) == covers._MAX_EDGE


# ------------------------------------------------------------------ #
# fetch_cover                                                          #
# ------------------------------------------------------------------ #

async def test_fetch_cover_stores_image_and_version(tmp_path):
    state_db = str(tmp_path / "state.db")
    vault = str(tmp_path / "vault")
    png = _png(600, 900)
    session = _FakeSession(_search_response(7777), _FakeResponse(png))

    info = await covers.fetch_cover(state_db, vault, "Pastoral", session=session)

    assert info["status"] == "ok"
    assert info["cover_path"] == "Pastoral/cover.jpg"
    written = tmp_path / "vault" / "Books" / "Pastoral" / "cover.jpg"
    assert written.is_file()

    assert info["version"] == hashlib.sha256(written.read_bytes()).hexdigest()[:12]
    assert len(info["version"]) == 12

    assert session.urls[1] == "https://covers.openlibrary.org/b/id/7777-L.jpg"
    assert covers.get_covers(state_db)["Pastoral"]["status"] == "ok"


async def test_fetch_cover_leaves_no_tmp_file(tmp_path):
    state_db = str(tmp_path / "state.db")
    vault = str(tmp_path / "vault")
    session = _FakeSession(_search_response(1), _FakeResponse(_png(400, 600)))

    await covers.fetch_cover(state_db, vault, "Pastoral", session=session)

    book_dir = tmp_path / "vault" / "Books" / "Pastoral"
    assert [p.name for p in book_dir.iterdir()] == ["cover.jpg"]
    assert not (book_dir / "cover.jpg.tmp").exists()


async def test_fetch_cover_records_none_for_tiny_placeholder(tmp_path):
    state_db = str(tmp_path / "state.db")
    vault = str(tmp_path / "vault")
    session = _FakeSession(_search_response(1), _FakeResponse(_png(1, 1)))

    info = await covers.fetch_cover(state_db, vault, "Pastoral", session=session)

    assert info["status"] == "none"
    assert info["cover_path"] == ""
    assert not (tmp_path / "vault" / "Books" / "Pastoral").exists()
    assert covers.get_covers(state_db)["Pastoral"]["status"] == "none"


async def test_fetch_cover_records_none_when_search_has_no_cover(tmp_path):
    state_db = str(tmp_path / "state.db")
    session = _FakeSession(_search_response(None))

    info = await covers.fetch_cover(
        state_db, str(tmp_path / "vault"), "Obscure Book", session=session
    )

    assert info["status"] == "none"
    assert len(session.urls) == 1  # no download attempted


async def test_fetch_cover_records_none_for_html_body(tmp_path):
    state_db = str(tmp_path / "state.db")
    html = b"<!DOCTYPE html><html><body>oops</body></html>"
    session = _FakeSession(_search_response(5), _FakeResponse(html))

    info = await covers.fetch_cover(
        state_db, str(tmp_path / "vault"), "Pastoral", session=session
    )

    assert info["status"] == "none"
    assert covers.get_covers(state_db)["Pastoral"]["status"] == "none"


# ------------------------------------------------------------------ #
# Candidate selection + staleness                                      #
# ------------------------------------------------------------------ #

def _seed_library(tmp_path):
    """State + koreader DBs holding one read book, one article, one unread book."""
    state_db = str(tmp_path / "state.db")
    koreader_db = str(tmp_path / "koreader.db")

    with sqlite3.connect(state_db) as conn:
        conn.execute("""
            CREATE TABLE document_aliases (
                hash TEXT PRIMARY KEY, title TEXT NOT NULL,
                filename TEXT DEFAULT '', resolved_by TEXT DEFAULT 'manual',
                kind TEXT DEFAULT 'book'
            )
        """)
        conn.execute("""
            CREATE TABLE synced_screenshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_path TEXT NOT NULL, content_hash TEXT NOT NULL,
                synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                book_title TEXT DEFAULT '', sync_date TEXT DEFAULT '2026-07-04',
                vault_png_path TEXT DEFAULT '',
                vault_root TEXT DEFAULT 'Books'
            )
        """)
        conn.executemany(
            "INSERT INTO document_aliases (hash, title, kind) VALUES (?,?,?)",
            [
                ("h_book", "Pastoral", "book"),
                ("h_article", "Some Long Read", "article"),
                ("h_unread", "Never Opened", "book"),
            ],
        )
        conn.executemany(
            "INSERT INTO synced_screenshots "
            "(device_path, content_hash, book_title, vault_root) VALUES (?,?,?,?)",
            [
                ("/screenshots/Fifteen-Dogs/a.bmp", "c1", "Fifteen-Dogs", "Books"),
                ("/screenshots/Some-Long-Read/a.bmp", "c2", "Some Long Read", "Articles"),
            ],
        )

    with sqlite3.connect(koreader_db) as conn:
        conn.execute("""
            CREATE TABLE progress_updates (
                id INTEGER PRIMARY KEY, document TEXT, percentage REAL DEFAULT 0.5,
                timestamp INTEGER DEFAULT (strftime('%s','now'))
            )
        """)
        conn.executemany(
            "INSERT INTO progress_updates (document) VALUES (?)",
            [("h_book",), ("h_article",)],
        )

    return state_db, koreader_db


def test_candidates_skip_articles_and_unread_books(tmp_path):
    state_db, koreader_db = _seed_library(tmp_path)
    assert set(covers._candidate_titles(state_db, koreader_db)) == {
        "Pastoral", "Fifteen-Dogs",
    }


def test_resolved_books_are_not_refetched(tmp_path):
    state_db, koreader_db = _seed_library(tmp_path)
    covers._store(state_db, "Pastoral", "Pastoral/cover.jpg", "abc123456789", "ok")
    assert covers._books_needing_covers(state_db, koreader_db) == ["Fifteen-Dogs"]


def test_recent_miss_is_not_refetched_but_a_stale_one_is(tmp_path):
    state_db, koreader_db = _seed_library(tmp_path)
    covers._store(state_db, "Pastoral", "", "", "none")
    covers._store(state_db, "Fifteen-Dogs", "", "", "none")

    assert covers._books_needing_covers(state_db, koreader_db) == []

    stale = f"-{covers._RETRY_NONE_DAYS + 1} days"
    with sqlite3.connect(state_db) as conn:
        conn.execute(
            "UPDATE book_covers SET updated_at = datetime('now', ?) WHERE title = ?",
            (stale, "Pastoral"),
        )

    assert covers._books_needing_covers(state_db, koreader_db) == ["Pastoral"]


async def test_resolve_book_covers_counts_and_skips_articles(tmp_path, monkeypatch):
    state_db, koreader_db = _seed_library(tmp_path)
    vault = str(tmp_path / "vault")
    asked: list[str] = []

    async def fake_fetch(sdb, vlt, title, session=None):
        asked.append(title)
        status = "ok" if title == "Pastoral" else "none"
        return covers._store(sdb, title, "", "", status)

    monkeypatch.setattr(covers, "fetch_cover", fake_fetch)

    result = await covers.resolve_book_covers(state_db, koreader_db, vault)

    assert sorted(asked) == ["Fifteen-Dogs", "Pastoral"]
    assert "Some Long Read" not in asked
    assert result == {"fetched": 1, "missing": 1, "skipped": 0}

    again = await covers.resolve_book_covers(state_db, koreader_db, vault)
    assert again == {"fetched": 0, "missing": 0, "skipped": 2}


async def test_resolve_book_covers_force_retries_everything(tmp_path, monkeypatch):
    state_db, koreader_db = _seed_library(tmp_path)
    covers._store(state_db, "Pastoral", "", "", "none")
    covers._store(state_db, "Fifteen-Dogs", "", "", "none")

    async def fake_fetch(sdb, vlt, title, session=None):
        return covers._store(sdb, title, "", "", "none")

    monkeypatch.setattr(covers, "fetch_cover", fake_fetch)

    result = await covers.resolve_book_covers(
        state_db, koreader_db, str(tmp_path / "vault"), force=True
    )
    assert result == {"fetched": 0, "missing": 2, "skipped": 0}


# ------------------------------------------------------------------ #
# API surface                                                          #
# ------------------------------------------------------------------ #

@pytest.fixture
def cover_api(tmp_path, monkeypatch):
    state_db, koreader_db = _seed_library(tmp_path)
    vault = tmp_path / "vault"
    vault.mkdir(exist_ok=True)
    monkeypatch.setenv("STATE_DB", state_db)
    monkeypatch.setenv("KOREADER_DB", koreader_db)
    monkeypatch.setenv("VAULT_PATH", str(vault))

    from xteink_service.koreader_sync import app
    return TestClient(app), state_db, vault


def test_list_books_exposes_a_relative_cover_url(cover_api):
    client, state_db, _vault = cover_api
    covers._store(state_db, "Pastoral", "Pastoral/cover.jpg", "deadbeef1234", "ok")

    books = {b["slug"]: b for b in client.get("/api/books").json()}

    assert books["pastoral"]["cover_url"] == "/api/books/pastoral/cover?v=deadbeef1234"
    assert not books["pastoral"]["cover_url"].startswith("http")
    assert books["fifteen-dogs"]["cover_url"] is None


def test_cover_endpoint_serves_jpeg_with_immutable_cache(cover_api):
    client, state_db, vault = cover_api
    data = covers._encode_cover(_png(600, 900))
    book_dir = vault / "Books" / "Pastoral"
    book_dir.mkdir(parents=True)
    (book_dir / "cover.jpg").write_bytes(data)
    covers._store(state_db, "Pastoral", "Pastoral/cover.jpg", "deadbeef1234", "ok")

    hit = client.get("/api/books/pastoral/cover?v=deadbeef1234")
    assert hit.status_code == 200
    assert hit.headers["content-type"] == "image/jpeg"
    assert hit.headers["cache-control"] == "public, max-age=31536000, immutable"

    stale = client.get("/api/books/pastoral/cover?v=oldversion00")
    assert stale.status_code == 200
    assert stale.headers["cache-control"] == "no-cache"


def test_cover_endpoint_404s_without_a_cover(cover_api):
    client, _state_db, _vault = cover_api
    r = client.get("/api/books/fifteen-dogs/cover")
    assert r.status_code == 404
    assert r.json() == {"detail": "no cover"}


def test_cover_endpoint_404s_when_the_file_is_gone(cover_api):
    client, state_db, _vault = cover_api
    covers._store(state_db, "Pastoral", "Pastoral/cover.jpg", "deadbeef1234", "ok")
    assert client.get("/api/books/pastoral/cover").status_code == 404


def test_covers_fetch_endpoint_returns_counts(cover_api, monkeypatch):
    client, _state_db, _vault = cover_api

    async def fake_fetch(sdb, vlt, title, session=None):
        return covers._store(sdb, title, "", "", "none")

    monkeypatch.setattr(covers, "fetch_cover", fake_fetch)

    r = client.post("/api/covers/fetch", json={"force": False})
    assert r.status_code == 200
    assert r.json() == {"fetched": 0, "missing": 2, "skipped": 0}
