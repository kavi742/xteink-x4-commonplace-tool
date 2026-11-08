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


# ------------------------------------------------------------------ #
# reconcile_kinds                                                      #
# ------------------------------------------------------------------ #

def _alias(conn, hash_, title, kind="book"):
    conn.execute(
        "INSERT INTO document_aliases (hash, title, kind) VALUES (?,?,?)",
        (hash_, title, kind),
    )
    conn.commit()


def test_reconcile_promotes_a_metadata_resolved_article(conn):
    """Send Metadata carries a bare basename, so it always records a book.
    The device walk is the only thing that can correct it."""
    _alias(conn, "h1", "How to Do Nothing", kind="book")

    assert ck.reconcile_kinds(conn, [("h1", "/Instapaper/How to Do Nothing.epub")]) == 1
    assert ck.kinds_by_hash(conn)["h1"] == "article"


def test_reconcile_demotes_a_file_moved_out_of_the_article_folder(conn):
    _alias(conn, "h1", "Keeper", kind="article")

    assert ck.reconcile_kinds(conn, [("h1", "/Books/Keeper.epub")]) == 1
    assert ck.kinds_by_hash(conn)["h1"] == "book"


def test_reconcile_is_a_no_op_when_the_kind_already_agrees(conn):
    _alias(conn, "h1", "Pastoral", kind="book")

    assert ck.reconcile_kinds(conn, [("h1", "/Books/Pastoral.epub")]) == 0


def test_reconcile_ignores_hashes_with_no_alias(conn):
    assert ck.reconcile_kinds(conn, [("nope", "/Instapaper/x.epub")]) == 0


def test_reconcile_tolerates_a_pre_migration_database():
    old = sqlite3.connect(":memory:")
    old.execute("CREATE TABLE document_aliases (hash TEXT PRIMARY KEY, title TEXT)")
    old.execute("INSERT INTO document_aliases VALUES ('h1', 'x')")
    old.commit()

    assert ck.reconcile_kinds(old, [("h1", "/Instapaper/x.epub")]) == 0


# ------------------------------------------------------------------ #
# device_files: closing the walk/push ordering race                    #
# ------------------------------------------------------------------ #

@pytest.fixture
def conn_with_files(conn):
    conn.execute("""
        CREATE TABLE device_files (
            hash TEXT PRIMARY KEY, path TEXT NOT NULL,
            seen_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
    """)
    return conn


def test_kind_survives_the_walk_so_a_later_push_can_use_it(conn_with_files):
    """The walk runs before the progress push creates the alias, so the path
    has to outlive the walk or the article is filed as a book."""
    ck.record_device_files(conn_with_files, [("h1", "/Instapaper/Random Numbers.epub")])

    assert ck.kind_from_device_files(conn_with_files, "h1") == "article"


def test_device_files_reports_none_for_a_file_never_seen(conn_with_files):
    assert ck.kind_from_device_files(conn_with_files, "unseen") is None


def test_device_files_path_is_refreshed_on_a_later_walk(conn_with_files):
    ck.record_device_files(conn_with_files, [("h1", "/Instapaper/x.epub")])
    ck.record_device_files(conn_with_files, [("h1", "/Books/x.epub")])

    assert ck.kind_from_device_files(conn_with_files, "h1") == "book"


def test_device_files_tolerate_a_pre_migration_database(conn):
    assert ck.record_device_files(conn, [("h1", "/Instapaper/x.epub")]) == 0
    assert ck.kind_from_device_files(conn, "h1") is None

