"""
Tests for Phase 8+9 API endpoints (no hardware required).

Covers: /status, /api/books, /api/books/{slug}/screenshots,
        /api/screenshots/{id}, /api/screenshots/{id}/image,
        PUT /api/screenshots/{id}, /api/reading-log,
        /api/aliases, PUT /api/aliases/{hash}, POST /api/vault/rebuild
"""
import io
import os
import sqlite3
from datetime import datetime
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image


# ------------------------------------------------------------------ #
# Fixtures                                                             #
# ------------------------------------------------------------------ #

@pytest.fixture
def api_env(tmp_path, monkeypatch):
    """
    Set up isolated DBs and vault, patch env vars so api.py reads them.
    Returns a dict with state_db, koreader_db, vault paths.
    """
    state_db    = str(tmp_path / "state.db")
    koreader_db = str(tmp_path / "koreader.db")
    vault       = tmp_path / "vault"
    vault.mkdir()

    monkeypatch.setenv("STATE_DB",    state_db)
    monkeypatch.setenv("KOREADER_DB", koreader_db)
    monkeypatch.setenv("VAULT_PATH",  str(vault))

    # Initialise tables
    with sqlite3.connect(state_db) as conn:
        conn.execute("""
            CREATE TABLE synced_screenshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_path TEXT NOT NULL, content_hash TEXT NOT NULL,
                synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                book_title TEXT DEFAULT '', sync_date TEXT DEFAULT '',
                ocr_text TEXT, vault_png_path TEXT DEFAULT '',
                ocr_corrected TEXT, user_notes TEXT,
                vault_root TEXT DEFAULT 'Books',
                UNIQUE(device_path, content_hash)
            )
        """)
        conn.execute("""
            CREATE TABLE document_aliases (
                hash TEXT PRIMARY KEY, title TEXT NOT NULL,
                filename TEXT DEFAULT '', resolved_by TEXT DEFAULT 'manual',
                computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                kind TEXT DEFAULT 'book'
            )
        """)

    with sqlite3.connect(koreader_db) as conn:
        conn.execute("""
            CREATE TABLE progress_updates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document TEXT NOT NULL, progress TEXT, percentage REAL,
                device TEXT, device_id TEXT, title TEXT, author TEXT,
                timestamp INTEGER DEFAULT (strftime('%s','now'))
            )
        """)

    return {"state_db": state_db, "koreader_db": koreader_db, "vault": vault}


def _seed_screenshot(state_db: str, vault: Path,
                     book: str = "Pastoral", n: int = 1,
                     root: str = "Books") -> int:
    """Insert a screenshot row and write a real PNG to the vault. Returns row id."""
    book_dir = vault / root / book
    book_dir.mkdir(parents=True, exist_ok=True)
    filename = f"2026-07-04-{n:02d}.png"
    png_path = book_dir / filename
    img = Image.new("RGB", (4, 4), color=(200, 200, 200))
    buf = io.BytesIO(); img.save(buf, format="PNG")
    png_path.write_bytes(buf.getvalue())

    embed = f"{book}/{filename}"
    with sqlite3.connect(state_db) as conn:
        cur = conn.execute(
            "INSERT INTO synced_screenshots "
            "(device_path, content_hash, book_title, sync_date, ocr_text, "
            "vault_png_path, vault_root) "
            "VALUES (?,?,?,?,?,?,?)",
            (f"/screenshots/{book}/shot{n}.bmp", f"hash{n:03d}",
             book, "2026-07-04", f"OCR text for shot {n}", embed, root),
        )
        return cur.lastrowid


def _seed_alias(state_db: str, doc_hash: str, title: str,
                kind: str = "book") -> None:
    with sqlite3.connect(state_db) as conn:
        conn.execute(
            "INSERT INTO document_aliases (hash, title, kind) VALUES (?,?,?)",
            (doc_hash, title, kind),
        )


def _seed_progress(koreader_db: str, doc_hash: str, pct: float = 0.25) -> None:
    with sqlite3.connect(koreader_db) as conn:
        conn.execute(
            "INSERT INTO progress_updates (document, progress, percentage) VALUES (?,?,?)",
            (doc_hash, "/body/DocFragment[5]/body", pct),
        )


@pytest.fixture
def client(api_env):
    """TestClient for the koreader_sync app (which includes api_router)."""
    # Import after env vars are set so api.py's os.getenv() picks them up
    from xteink_service.koreader_sync import app
    return TestClient(app)


# ------------------------------------------------------------------ #
# /status                                                              #
# ------------------------------------------------------------------ #

def test_status_empty(client):
    r = client.get("/status")
    assert r.status_code == 200
    body = r.json()
    assert "screenshots" in body
    assert "koreader" in body
    assert body["screenshots"]["total"] == 0


def test_status_with_data(client, api_env):
    _seed_screenshot(api_env["state_db"], api_env["vault"])
    _seed_alias(api_env["state_db"], "abc123", "Pastoral")
    _seed_progress(api_env["koreader_db"], "abc123", 0.45)

    r = client.get("/status")
    assert r.status_code == 200
    body = r.json()
    assert body["screenshots"]["total"] == 1
    assert body["koreader"]["total_updates"] == 1
    assert body["koreader"]["recent"][0]["title"] == "Pastoral"
    assert body["koreader"]["recent"][0]["percentage"] == 45.0


# ------------------------------------------------------------------ #
# /api/books                                                           #
# ------------------------------------------------------------------ #

def test_list_books_empty(client):
    r = client.get("/api/books")
    assert r.status_code == 200
    assert r.json() == []


def test_list_books(client, api_env):
    _seed_screenshot(api_env["state_db"], api_env["vault"], "Pastoral", 1)
    _seed_screenshot(api_env["state_db"], api_env["vault"], "Pastoral", 2)
    _seed_screenshot(api_env["state_db"], api_env["vault"], "Fifteen Dogs", 1)

    r = client.get("/api/books")
    assert r.status_code == 200
    books = {b["book_title"]: b for b in r.json()}
    assert books["Pastoral"]["screenshot_count"] == 2
    assert books["Fifteen Dogs"]["screenshot_count"] == 1


def test_list_books_includes_books_with_only_reading_progress(client, api_env):
    """A book read on a device that takes no screenshots (a Kindle) still shows."""
    _seed_screenshot(api_env["state_db"], api_env["vault"], "Pastoral", 1)
    with sqlite3.connect(api_env["state_db"]) as conn:
        conn.execute("INSERT INTO document_aliases (hash, title) VALUES (?,?)",
                     ("kindlehash", "Son of Nobody"))
    with sqlite3.connect(api_env["koreader_db"]) as conn:
        conn.execute("INSERT INTO progress_updates "
                     "(document, progress, percentage, device, timestamp) VALUES (?,?,?,?,?)",
                     ("kindlehash", "/body/p[1]", 0.42, "KindleVoyage", 1790531387))

    books = {b["book_title"]: b for b in client.get("/api/books").json()}
    assert books["Son of Nobody"]["screenshot_count"] == 0
    assert books["Son of Nobody"]["percentage_display"] == 42.0
    assert books["Pastoral"]["screenshot_count"] == 1


def test_list_books_does_not_list_unread_device_books(client, api_env):
    """The File Transfer preload aliases every epub on the SD card; only books
    with actual progress belong in the list."""
    with sqlite3.connect(api_env["state_db"]) as conn:
        conn.execute("INSERT INTO document_aliases (hash, title) VALUES (?,?)",
                     ("neverread", "Some Book Sitting On The Card"))

    assert client.get("/api/books").json() == []


def test_list_books_merges_screenshots_and_progress_for_one_book(client, api_env):
    """Screenshot folder name and Calibre-style alias title are the same book."""
    _seed_screenshot(api_env["state_db"], api_env["vault"], "The-Hidden-Keys", 1)
    with sqlite3.connect(api_env["state_db"]) as conn:
        conn.execute("INSERT INTO document_aliases (hash, title) VALUES (?,?)",
                     ("h1", "Hidden Keys, The - Andre Alexis"))
    with sqlite3.connect(api_env["koreader_db"]) as conn:
        conn.execute("INSERT INTO progress_updates "
                     "(document, progress, percentage, timestamp) VALUES (?,?,?,?)",
                     ("h1", "/body/p[1]", 0.6, 1790531387))

    books = client.get("/api/books").json()
    assert len(books) == 1
    assert books[0]["screenshot_count"] == 1
    assert books[0]["percentage_display"] == 60.0


# ------------------------------------------------------------------ #
# /api/books/{slug}/screenshots                                        #
# ------------------------------------------------------------------ #

def test_list_screenshots_for_book(client, api_env):
    _seed_screenshot(api_env["state_db"], api_env["vault"], "Pastoral", 1)
    _seed_screenshot(api_env["state_db"], api_env["vault"], "Pastoral", 2)

    r = client.get("/api/books/Pastoral/screenshots")
    assert r.status_code == 200
    shots = r.json()
    assert len(shots) == 2
    assert shots[0]["book_title"] == "Pastoral"


def test_list_screenshots_not_found(client):
    r = client.get("/api/books/NoSuchBook/screenshots")
    assert r.status_code == 404


# ------------------------------------------------------------------ #
# /api/screenshots/{id}                                               #
# ------------------------------------------------------------------ #

def test_get_screenshot(client, api_env):
    row_id = _seed_screenshot(api_env["state_db"], api_env["vault"])

    r = client.get(f"/api/screenshots/{row_id}")
    assert r.status_code == 200
    assert r.json()["book_title"] == "Pastoral"


def test_get_screenshot_not_found(client):
    r = client.get("/api/screenshots/99999")
    assert r.status_code == 404


# ------------------------------------------------------------------ #
# /api/screenshots/{id}/image                                         #
# ------------------------------------------------------------------ #

def test_get_screenshot_image(client, api_env):
    row_id = _seed_screenshot(api_env["state_db"], api_env["vault"])

    r = client.get(f"/api/screenshots/{row_id}/image")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"
    # Verify it's a valid PNG
    img = Image.open(io.BytesIO(r.content))
    assert img.format == "PNG"


def test_get_screenshot_image_not_found(client):
    r = client.get("/api/screenshots/99999/image")
    assert r.status_code == 404


# ------------------------------------------------------------------ #
# PUT /api/screenshots/{id}                                           #
# ------------------------------------------------------------------ #

def test_update_screenshot_ocr(client, api_env):
    row_id = _seed_screenshot(api_env["state_db"], api_env["vault"])

    r = client.put(f"/api/screenshots/{row_id}",
                   json={"ocr_corrected": "Corrected text here"})
    assert r.status_code == 200

    r2 = client.get(f"/api/screenshots/{row_id}")
    assert r2.json()["ocr_corrected"] == "Corrected text here"


def test_update_screenshot_notes(client, api_env):
    row_id = _seed_screenshot(api_env["state_db"], api_env["vault"])

    r = client.put(f"/api/screenshots/{row_id}",
                   json={"user_notes": "Great passage about sheep"})
    assert r.status_code == 200

    r2 = client.get(f"/api/screenshots/{row_id}")
    assert r2.json()["user_notes"] == "Great passage about sheep"


def test_update_screenshot_empty_body(client, api_env):
    row_id = _seed_screenshot(api_env["state_db"], api_env["vault"])
    r = client.put(f"/api/screenshots/{row_id}", json={})
    assert r.status_code == 400


# ------------------------------------------------------------------ #
# /api/reading-log                                                     #
# ------------------------------------------------------------------ #

def test_reading_log_empty(client):
    r = client.get("/api/reading-log")
    assert r.status_code == 200
    assert r.json() == []


def test_reading_log_with_alias(client, api_env):
    doc_hash = "abc123def456"
    _seed_alias(api_env["state_db"], doc_hash, "Fifteen Dogs")
    _seed_progress(api_env["koreader_db"], doc_hash, 0.12)

    r = client.get("/api/reading-log")
    assert r.status_code == 200
    entries = r.json()
    assert len(entries) == 1
    assert entries[0]["title_resolved"] == "Fifteen Dogs"
    assert entries[0]["percentage_display"] == 12.0


def test_reading_log_marks_finishing_entry(client, api_env):
    doc = "finish123"
    _seed_alias(api_env["state_db"], doc, "The Odyssey")
    for pct in (0.50, 0.96, 0.99):  # inserted as ids 1, 2, 3
        _seed_progress(api_env["koreader_db"], doc, pct)

    entries = client.get("/api/reading-log").json()
    by_id = {e["id"]: e for e in entries}
    # The first update to cross the 0.95 threshold finishes the book.
    assert by_id[2]["finishes_book"] is True
    assert by_id[1]["finishes_book"] is False
    assert by_id[3]["finishes_book"] is False
    # Exactly one finishing entry per book.
    assert sum(1 for e in entries if e["finishes_book"]) == 1


def test_reading_log_unfinished_has_no_star(client, api_env):
    _seed_progress(api_env["koreader_db"], "wip456", 0.40)
    entries = client.get("/api/reading-log").json()
    assert entries and all(e["finishes_book"] is False for e in entries)


# ------------------------------------------------------------------ #
# Book identity — screenshots + reading log share one URL              #
# ------------------------------------------------------------------ #

def test_book_page_unifies_screenshots_and_reading_log(client, api_env):
    """A screenshot-folder slug also serves the reading data even though the
    alias title is the (different) Calibre library form."""
    _seed_screenshot(api_env["state_db"], api_env["vault"], "The-Hidden-Keys", 1)
    _seed_alias(api_env["state_db"], "hk123", "Hidden Keys, The - Andre Alexis")
    _seed_progress(api_env["koreader_db"], "hk123", 0.42)

    shots = client.get("/api/books/The-Hidden-Keys/screenshots").json()
    assert len(shots) == 1

    stats = client.get("/api/books/The-Hidden-Keys/reading-stats").json()
    assert stats["current_pct"] == 42.0

    cal = client.get("/api/books/The-Hidden-Keys/reading-calendar").json()
    assert cal and cal[-1]["end_pct"] == 42.0


def test_reading_log_book_slug_points_to_screenshot_page(client, api_env):
    """The reading-log link resolves to the screenshot book's URL, not the
    alias title, so both land on the same page."""
    _seed_screenshot(api_env["state_db"], api_env["vault"], "The-Hidden-Keys", 1)
    _seed_alias(api_env["state_db"], "hk123", "Hidden Keys, The - Andre Alexis")
    _seed_progress(api_env["koreader_db"], "hk123", 0.42)

    entry = client.get("/api/reading-log").json()[0]
    assert entry["title_resolved"] == "Hidden Keys, The - Andre Alexis"
    assert entry["book_slug"] == "The-Hidden-Keys"


def test_reading_log_book_slug_falls_back_to_title(client, api_env):
    """A reading-only book (no screenshots) links by its resolved title."""
    _seed_alias(api_env["state_db"], "ro1", "Some Book - Someone")
    _seed_progress(api_env["koreader_db"], "ro1", 0.10)

    entry = client.get("/api/reading-log").json()[0]
    assert entry["book_slug"] == "Some Book - Someone"


# ------------------------------------------------------------------ #
# /api/aliases                                                         #
# ------------------------------------------------------------------ #

def test_list_aliases_empty(client):
    r = client.get("/api/aliases")
    assert r.status_code == 200
    assert r.json() == []


def test_list_aliases(client, api_env):
    _seed_alias(api_env["state_db"], "hash1", "Book A")
    _seed_alias(api_env["state_db"], "hash2", "Book B")

    r = client.get("/api/aliases")
    titles = [a["title"] for a in r.json()]
    assert "Book A" in titles
    assert "Book B" in titles


def test_put_alias(client, api_env):
    r = client.put("/api/aliases/newhash123",
                   json={"title": "My New Book", "filename": "my-new-book.epub"})
    assert r.status_code == 200
    assert r.json()["title"] == "My New Book"

    r2 = client.get("/api/aliases")
    hashes = {a["hash"]: a["title"] for a in r2.json()}
    assert hashes["newhash123"] == "My New Book"


def test_put_alias_overwrites(client, api_env):
    _seed_alias(api_env["state_db"], "existinghash", "Old Title")
    r = client.put("/api/aliases/existinghash", json={"title": "New Title"})
    assert r.status_code == 200

    r2 = client.get("/api/aliases")
    hashes = {a["hash"]: a["title"] for a in r2.json()}
    assert hashes["existinghash"] == "New Title"


# ------------------------------------------------------------------ #
# POST /api/vault/rebuild                                              #
# ------------------------------------------------------------------ #

def test_vault_rebuild(client, api_env):
    _seed_screenshot(api_env["state_db"], api_env["vault"], "Pastoral", 1)
    _seed_screenshot(api_env["state_db"], api_env["vault"], "Pastoral", 2)

    r = client.post("/api/vault/rebuild")
    assert r.status_code == 200
    assert r.json()["rebuilt_notes"] == 2


# ------------------------------------------------------------------ #
# Articles                                                             #
# ------------------------------------------------------------------ #

def _seed_article(api_env, title: str, doc_hash: str,
                  pct: float | None = None, ts: int = 1790531387,
                  screenshots: int = 0) -> None:
    """An article alias plus optional progress and Articles/ screenshots."""
    _seed_alias(api_env["state_db"], doc_hash, title, kind="article")
    if pct is not None:
        with sqlite3.connect(api_env["koreader_db"]) as conn:
            conn.execute(
                "INSERT INTO progress_updates "
                "(document, progress, percentage, timestamp) VALUES (?,?,?,?)",
                (doc_hash, "/body/p[1]", pct, ts),
            )
    for n in range(1, screenshots + 1):
        _seed_screenshot(api_env["state_db"], api_env["vault"], title, n,
                         root="Articles")


def test_books_list_collapses_articles_into_one_entry(client, api_env):
    _seed_screenshot(api_env["state_db"], api_env["vault"], "Pastoral", 1)
    _seed_article(api_env, "How to Do Nothing", "art1", pct=0.4, screenshots=2)
    _seed_article(api_env, "The Attention Economy", "art2", pct=0.98,
                  ts=1790531400, screenshots=1)
    _seed_article(api_env, "A Third Piece", "art3", pct=0.1, ts=1790531300)

    entries = {b["book_title"]: b for b in client.get("/api/books").json()}
    assert set(entries) == {"Pastoral", "Articles"}

    articles = entries["Articles"]
    assert articles["slug"] == "articles"
    assert articles["kind"] == "articles"
    assert articles["article_count"] == 3
    assert articles["screenshot_count"] == 3
    assert articles["percentage_display"] is None
    assert articles["last_read_at"] == 1790531400
    assert entries["Pastoral"]["kind"] == "book"
    assert entries["Pastoral"]["slug"] == "pastoral"


def test_books_list_has_no_articles_entry_without_articles(client, api_env):
    _seed_screenshot(api_env["state_db"], api_env["vault"], "Pastoral", 1)
    books = client.get("/api/books").json()
    assert [b["book_title"] for b in books] == ["Pastoral"]
    assert "article_count" not in books[0]


def test_list_articles_shape_and_order(client, api_env):
    _seed_article(api_env, "How to Do Nothing", "art1", pct=0.4,
                  ts=1790531300, screenshots=2)
    _seed_article(api_env, "The Attention Economy", "art2", pct=0.98,
                  ts=1790531400)
    _seed_article(api_env, "Never Opened", "art3")  # alias only: no activity

    articles = client.get("/api/articles").json()
    assert [a["title"] for a in articles] == [
        "The Attention Economy", "How to Do Nothing",
    ]
    assert set(articles[0]) == {
        "slug", "title", "hashes", "screenshot_count", "clipping_count",
        "percentage_display", "last_read_at", "finished", "total_pages", "page",
    }
    first, second = articles
    assert first["finished"] is True
    assert first["percentage_display"] == 98.0
    assert second["finished"] is False
    assert second["screenshot_count"] == 2
    assert second["slug"] == "how-to-do-nothing"
    assert second["hashes"] == ["art1"]
    assert second["clipping_count"] == 0


def test_get_article_detail(client, api_env):
    _seed_article(api_env, "How to Do Nothing", "art1", pct=0.4, screenshots=2)

    body = client.get("/api/articles/how-to-do-nothing").json()
    assert set(body) == {
        "slug", "title", "hashes", "percentage_display", "last_read_at",
        "finished", "total_pages", "page", "screenshots", "clippings",
    }
    assert body["title"] == "How to Do Nothing"
    assert len(body["screenshots"]) == 2
    assert body["screenshots"][0]["book_title"] == "How to Do Nothing"
    assert body["clippings"] == []


def test_get_article_unknown_slug(client, api_env):
    assert client.get("/api/articles/nope").status_code == 404


def test_article_titles_that_collide_as_books_stay_separate(client, api_env):
    """Book-mode slugging would merge these two into one article."""
    _seed_article(api_env, "Attention - The Atlantic", "art1", pct=0.3)
    _seed_article(api_env, "Attention - Wired", "art2", pct=0.6, ts=1790531400)

    slugs = {a["slug"] for a in client.get("/api/articles").json()}
    assert slugs == {"attention-the-atlantic", "attention-wired"}


def test_book_endpoints_do_not_serve_the_reserved_articles_slug(client, api_env):
    _seed_article(api_env, "How to Do Nothing", "art1", pct=0.4, screenshots=1)

    assert client.get("/api/books/articles/screenshots").status_code == 404
    assert client.get("/api/books/articles/reading-calendar").json() == []
    assert client.get("/api/books/articles/highlights").json() == []
    assert client.get("/api/books/articles/reading-stats").json()["total_pages"] is None


def test_article_screenshots_are_not_served_as_a_book(client, api_env):
    _seed_article(api_env, "How to Do Nothing", "art1", pct=0.4, screenshots=1)
    assert client.get("/api/books/How to Do Nothing/screenshots").status_code == 404


def test_article_image_is_served_from_the_articles_folder(client, api_env):
    _seed_article(api_env, "How to Do Nothing", "art1", pct=0.4, screenshots=1)
    shot = client.get("/api/articles/how-to-do-nothing").json()["screenshots"][0]

    r = client.get(f"/api/screenshots/{shot['id']}/image")
    assert r.status_code == 200
    assert r.headers["content-type"] == "image/png"


def test_put_alias_keeps_the_article_kind(client, api_env):
    _seed_alias(api_env["state_db"], "art1", "How to Do Nothing", kind="article")
    assert client.put("/api/aliases/art1", json={"title": "How To Do Nothing"}).status_code == 200

    by_hash = {a["hash"]: a for a in client.get("/api/aliases").json()}
    assert by_hash["art1"]["kind"] == "article"


def test_aliases_never_mark_articles_as_linked(client, api_env):
    _seed_alias(api_env["state_db"], "art1", "Attention - The Atlantic", kind="article")
    _seed_alias(api_env["state_db"], "art2", "Attention - Wired", kind="article")

    assert all(not a["linked"] for a in client.get("/api/aliases").json())


# ------------------------------------------------------------------ #
# /api/reading-stats                                                   #
# ------------------------------------------------------------------ #

def _seed_finished_today(api_env, doc_hash: str, title: str, kind: str,
                         total_pages: int) -> None:
    _seed_alias(api_env["state_db"], doc_hash, title, kind=kind)
    with sqlite3.connect(api_env["state_db"]) as conn:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS book_pages ("
            "title TEXT PRIMARY KEY, total_pages INTEGER NOT NULL, "
            "source TEXT DEFAULT 'openlibrary', updated_at TIMESTAMP)"
        )
        conn.execute(
            "INSERT INTO book_pages (title, total_pages, source) VALUES (?,?,?)",
            (title, total_pages, "estimate"),
        )
    now = int(datetime.now().timestamp())
    with sqlite3.connect(api_env["koreader_db"]) as conn:
        conn.execute(
            "INSERT INTO progress_updates "
            "(document, progress, percentage, timestamp) VALUES (?,?,?,?)",
            (doc_hash, "/body/p[1]", 1.0, now),
        )


def test_reading_stats_separates_articles_from_books(client, api_env):
    _seed_finished_today(api_env, "b1", "Pastoral", "book", 200)
    _seed_finished_today(api_env, "a1", "How to Do Nothing", "article", 20)

    stats = client.get("/api/reading-stats").json()

    assert stats["books"] == {"started": 1, "in_progress": 0, "finished": 1}
    assert stats["articles"] == {"started": 1, "in_progress": 0, "finished": 1}
    assert stats["books_read"]["today"] == 1
    assert stats["books_read"]["all_time"] == 1
    assert stats["articles_read"] == {
        "today": 1, "week": 1, "month": 1, "year": 1, "all_time": 1,
    }
    # The article's pages count as reading; its percentage does not.
    assert stats["pages_read"]["today"] == 220
    assert stats["read_pct"]["today"] == 100.0


def test_reading_stats_without_articles_is_unchanged(client, api_env):
    _seed_finished_today(api_env, "b1", "Pastoral", "book", 200)

    stats = client.get("/api/reading-stats").json()
    assert stats["articles"] == {"started": 0, "in_progress": 0, "finished": 0}
    assert stats["articles_read"]["all_time"] == 0
    assert stats["pages_read"]["today"] == 200
    assert stats["books_read"]["today"] == 1


# ------------------------------------------------------------------ #
# Backward compatibility                                               #
# ------------------------------------------------------------------ #

def test_api_reads_a_database_without_the_new_columns(tmp_path, monkeypatch):
    """A deployment that has not run the migration yet must still serve."""
    state_db = str(tmp_path / "old-state.db")
    koreader_db = str(tmp_path / "old-koreader.db")
    monkeypatch.setenv("STATE_DB", state_db)
    monkeypatch.setenv("KOREADER_DB", koreader_db)
    monkeypatch.setenv("VAULT_PATH", str(tmp_path / "vault"))

    with sqlite3.connect(state_db) as conn:
        conn.execute("""
            CREATE TABLE synced_screenshots (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                device_path TEXT NOT NULL, content_hash TEXT NOT NULL,
                synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                book_title TEXT DEFAULT '', sync_date TEXT DEFAULT '',
                ocr_text TEXT, vault_png_path TEXT DEFAULT '',
                ocr_corrected TEXT, user_notes TEXT,
                UNIQUE(device_path, content_hash)
            )
        """)
        conn.execute("""
            CREATE TABLE document_aliases (
                hash TEXT PRIMARY KEY, title TEXT NOT NULL,
                filename TEXT DEFAULT '', resolved_by TEXT DEFAULT 'manual',
                computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute(
            "INSERT INTO synced_screenshots (device_path, content_hash, book_title, "
            "vault_png_path) VALUES ('/screenshots/Pastoral/a.bmp', 'h1', "
            "'Pastoral', 'Pastoral/2026-07-04-01.png')"
        )
        conn.execute("INSERT INTO document_aliases (hash, title) VALUES ('h1', 'Pastoral')")
    with sqlite3.connect(koreader_db) as conn:
        conn.execute("""
            CREATE TABLE progress_updates (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                document TEXT NOT NULL, progress TEXT, percentage REAL,
                device TEXT, device_id TEXT, title TEXT, author TEXT,
                timestamp INTEGER DEFAULT (strftime('%s','now'))
            )
        """)
        conn.execute(
            "INSERT INTO progress_updates (document, percentage) VALUES ('h1', 0.5)"
        )

    from xteink_service.koreader_sync import app
    c = TestClient(app)
    books = c.get("/api/books").json()
    assert [b["book_title"] for b in books] == ["Pastoral"]
    assert books[0]["kind"] == "book"
    assert c.get("/api/articles").json() == []
    assert c.get("/api/reading-stats").json()["articles"]["started"] == 0
    assert c.get("/api/books/Pastoral/screenshots").status_code == 200
