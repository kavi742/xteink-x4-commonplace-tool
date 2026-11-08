"""Tests for the book/article split.

Articles arrive through the same channels as books, so everything downstream
keys off the device directory recorded when the alias was created.
"""
import sqlite3

import pytest

from xteink_service import content_kind as ck


@pytest.fixture
def conn():
    c = sqlite3.connect(":memory:")
    c.execute("""
        CREATE TABLE document_aliases (
            hash TEXT PRIMARY KEY, title TEXT NOT NULL,
            filename TEXT DEFAULT '', resolved_by TEXT DEFAULT 'manual',
            computed_at TIMESTAMP, kind TEXT DEFAULT 'book'
        )
    """)
    return c


# ------------------------------------------------------------------ #
# kind_for_device_path                                                 #
# ------------------------------------------------------------------ #

@pytest.mark.parametrize("path, expected", [
    ("/Instapaper/How to Do Nothing.epub", "article"),
    ("/instapaper/how-to-do-nothing.epub", "article"),   # FAT is case-insensitive
    ("/INSTAPAPER/x.epub", "article"),
    ("/Instapaper/2026/x.epub", "article"),              # nested under the prefix
    ("/Books/Pastoral.epub", "book"),
    ("/Pastoral.epub", "book"),
    ("Pastoral.epub", "book"),                           # bare filename, no directory
    ("/InstapaperArchive/x.epub", "book"),               # prefix must end at a segment
    ("", "book"),
])
def test_kind_for_device_path(path, expected):
    assert ck.kind_for_device_path(path) == expected


def test_custom_prefixes_from_env(monkeypatch):
    monkeypatch.setenv("ARTICLE_PATH_PREFIXES", "/Pocket, Wallabag/")
    assert ck.kind_for_device_path("/Pocket/x.epub") == "article"
    assert ck.kind_for_device_path("/Wallabag/x.epub") == "article"
    # The default no longer applies once the env var is set.
    assert ck.kind_for_device_path("/Instapaper/x.epub") == "book"


def test_blank_env_falls_back_to_the_default(monkeypatch):
    monkeypatch.setenv("ARTICLE_PATH_PREFIXES", "")
    assert ck.kind_for_device_path("/Instapaper/x.epub") == "article"


# ------------------------------------------------------------------ #
# Alias lookups                                                        #
# ------------------------------------------------------------------ #

def test_kinds_by_hash(conn):
    conn.executemany(
        "INSERT INTO document_aliases (hash, title, kind) VALUES (?,?,?)",
        [("h1", "Pastoral", "book"), ("h2", "How to Do Nothing", "article")],
    )
    assert ck.kinds_by_hash(conn) == {"h1": "book", "h2": "article"}


def test_kinds_by_hash_without_the_column_defaults_to_book():
    """A database that predates the migration must still be readable."""
    old = sqlite3.connect(":memory:")
    old.execute("CREATE TABLE document_aliases (hash TEXT PRIMARY KEY, title TEXT)")
    old.execute("INSERT INTO document_aliases VALUES ('h1', 'Pastoral')")
    assert ck.kinds_by_hash(old) == {"h1": "book"}


def test_kinds_by_hash_without_the_table():
    assert ck.kinds_by_hash(sqlite3.connect(":memory:")) == {}


def test_kind_for_title_matches_the_screenshot_folder_name(conn):
    conn.execute(
        "INSERT INTO document_aliases (hash, title, kind) VALUES (?,?,?)",
        ("h2", "How to Do Nothing", "article"),
    )
    # The device hyphenates spaces in screenshot folder names.
    assert ck.kind_for_title(conn, "How-to-Do-Nothing") == "article"
    assert ck.kind_for_title(conn, "Pastoral") == "book"
    assert ck.kind_for_title(conn, "") == "book"


def test_kind_for_title_ignores_book_aliases(conn):
    conn.execute(
        "INSERT INTO document_aliases (hash, title, kind) VALUES (?,?,?)",
        ("h1", "Pastoral", "book"),
    )
    assert ck.kind_for_title(conn, "Pastoral") == "book"


# ------------------------------------------------------------------ #
# Vault roots                                                          #
# ------------------------------------------------------------------ #

def test_vault_root_round_trip():
    assert ck.vault_root_for("article") == "Articles"
    assert ck.vault_root_for("book") == "Books"
    assert ck.kind_for_vault_root("Articles") == "article"
    assert ck.kind_for_vault_root("Books") == "book"
    assert ck.kind_for_vault_root(None) == "book"
