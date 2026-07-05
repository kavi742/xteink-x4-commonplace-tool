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
                     book: str = "Pastoral", n: int = 1) -> int:
    """Insert a screenshot row and write a real PNG to the vault. Returns row id."""
    book_dir = vault / "Books" / book
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
            "(device_path, content_hash, book_title, sync_date, ocr_text, vault_png_path) "
            "VALUES (?,?,?,?,?,?)",
            (f"/screenshots/{book}/shot{n}.bmp", f"hash{n:03d}",
             book, "2026-07-04", f"OCR text for shot {n}", embed),
        )
        return cur.lastrowid


def _seed_alias(state_db: str, doc_hash: str, title: str) -> None:
    with sqlite3.connect(state_db) as conn:
        conn.execute(
            "INSERT INTO document_aliases (hash, title) VALUES (?,?)",
            (doc_hash, title),
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
