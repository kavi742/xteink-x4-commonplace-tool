import sqlite3

import pytest
from xteink_service.state import SyncState


@pytest.fixture
def state(tmp_path):
    return SyncState(str(tmp_path / "state.db"))


def test_not_synced_initially(state):
    assert not state.is_path_synced("/screenshots/Book/file.bmp")
    assert not state.is_synced("/screenshots/Book/file.bmp", "abc123")


def test_mark_and_check_path(state):
    state.mark_synced("/screenshots/Book/a.bmp", "hash1", "Book", "2026-07-04")
    assert state.is_path_synced("/screenshots/Book/a.bmp")
    assert not state.is_path_synced("/screenshots/Book/b.bmp")


def test_mark_and_check_exact(state):
    state.mark_synced("/screenshots/Book/a.bmp", "hash1")
    assert state.is_synced("/screenshots/Book/a.bmp", "hash1")
    assert not state.is_synced("/screenshots/Book/a.bmp", "different_hash")


def test_idempotent_mark(state):
    """Marking the same (path, hash) twice must not raise or duplicate."""
    state.mark_synced("/screenshots/Book/a.bmp", "hash1")
    state.mark_synced("/screenshots/Book/a.bmp", "hash1")  # second call is a no-op
    assert state.is_path_synced("/screenshots/Book/a.bmp")


def test_different_files_independent(state):
    state.mark_synced("/screenshots/A/a.bmp", "h1", "A", "2026-07-04")
    state.mark_synced("/screenshots/B/b.bmp", "h2", "B", "2026-07-04")
    assert state.is_path_synced("/screenshots/A/a.bmp")
    assert state.is_path_synced("/screenshots/B/b.bmp")
    assert not state.is_path_synced("/screenshots/C/c.bmp")


def test_get_title_unknown_hash(state):
    assert state.get_title("unknownhash") is None


def test_get_title_after_alias(state):
    with __import__("sqlite3").connect(state.db_path) as conn:
        conn.execute(
            "INSERT INTO document_aliases (hash, title) VALUES (?, ?)",
            ("abc123", "Pastoral"),
        )
    assert state.get_title("abc123") == "Pastoral"


# ------------------------------------------------------------------ #
# Content kind                                                         #
# ------------------------------------------------------------------ #

def _columns(db_path: str, table: str) -> set[str]:
    with sqlite3.connect(db_path) as conn:
        return {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}


def test_set_title_defaults_to_book(state):
    state.set_title("h1", "Pastoral")
    with sqlite3.connect(state.db_path) as conn:
        kind = conn.execute(
            "SELECT kind FROM document_aliases WHERE hash = 'h1'"
        ).fetchone()[0]
    assert kind == "book"


def test_set_title_records_an_article(state):
    state.set_title("h2", "How to Do Nothing", kind="article")
    with sqlite3.connect(state.db_path) as conn:
        kind = conn.execute(
            "SELECT kind FROM document_aliases WHERE hash = 'h2'"
        ).fetchone()[0]
    assert kind == "article"


def test_mark_synced_records_the_vault_root(state):
    state.mark_synced("/screenshots/Art/a.bmp", "h1", "Art", "2026-07-04",
                      vault_root="Articles")
    with sqlite3.connect(state.db_path) as conn:
        root = conn.execute(
            "SELECT vault_root FROM synced_screenshots WHERE content_hash = 'h1'"
        ).fetchone()[0]
    assert root == "Articles"


def test_mark_synced_defaults_to_books(state):
    state.mark_synced("/screenshots/Book/a.bmp", "h1")
    with sqlite3.connect(state.db_path) as conn:
        root = conn.execute(
            "SELECT vault_root FROM synced_screenshots WHERE content_hash = 'h1'"
        ).fetchone()[0]
    assert root == "Books"


# ------------------------------------------------------------------ #
# Migration of a pre-articles database                                 #
# ------------------------------------------------------------------ #

def _old_schema_db(path: str) -> None:
    """The schema as it stood before articles existed."""
    with sqlite3.connect(path) as conn:
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


def test_old_db_gains_the_new_columns_without_losing_rows(tmp_path):
    db = str(tmp_path / "old.db")
    _old_schema_db(db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO synced_screenshots (device_path, content_hash, book_title) "
            "VALUES ('/screenshots/Pastoral/a.bmp', 'h1', 'Pastoral')"
        )
        conn.execute(
            "INSERT INTO document_aliases (hash, title, filename) "
            "VALUES ('a1', 'Pastoral', 'Pastoral.epub')"
        )

    state = SyncState(db)

    assert "vault_root" in _columns(db, "synced_screenshots")
    assert "kind" in _columns(db, "document_aliases")
    assert state.is_path_synced("/screenshots/Pastoral/a.bmp")
    assert state.get_title("a1") == "Pastoral"
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT kind FROM document_aliases WHERE hash = 'a1'"
        ).fetchone()[0] == "book"
        assert conn.execute(
            "SELECT vault_root FROM synced_screenshots WHERE content_hash = 'h1'"
        ).fetchone()[0] == "Books"


def test_migration_backfills_aliases_that_recorded_a_device_path(tmp_path):
    db = str(tmp_path / "old.db")
    _old_schema_db(db)
    with sqlite3.connect(db) as conn:
        conn.executemany(
            "INSERT INTO document_aliases (hash, title, filename) VALUES (?,?,?)",
            [
                ("a1", "How to Do Nothing", "/Instapaper/How to Do Nothing.epub"),
                ("a2", "Pastoral", "/Books/Pastoral.epub"),
                # No directory, so no evidence either way: left alone.
                ("a3", "Fifteen Dogs", "Fifteen Dogs.epub"),
            ],
        )

    SyncState(db)

    with sqlite3.connect(db) as conn:
        kinds = dict(conn.execute("SELECT hash, kind FROM document_aliases"))
    assert kinds == {"a1": "article", "a2": "book", "a3": "book"}


def test_migration_is_idempotent(tmp_path):
    db = str(tmp_path / "old.db")
    _old_schema_db(db)
    SyncState(db)
    SyncState(db)  # second open must not raise
    assert "kind" in _columns(db, "document_aliases")
