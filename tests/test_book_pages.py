"""Tests for xteink_service.book_pages — page-count lookup + caching.

Unit tests build synthetic state/koreader DBs in a temp dir. The live test hits
Open Library (skipped if the network is unreachable).
"""
import sqlite3
import urllib.error
import urllib.request

import pytest

from xteink_service import book_pages


def _seed_state(path: str) -> None:
    """Create document_aliases with two read books + one unread."""
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE document_aliases (
            hash TEXT PRIMARY KEY, title TEXT NOT NULL, filename TEXT DEFAULT '',
            resolved_by TEXT DEFAULT 'manual', computed_at TIMESTAMP
        )
    """)
    conn.executemany(
        "INSERT INTO document_aliases (hash, title, filename) VALUES (?,?,?)",
        [
            ("h_read1", "Fifteen-Dogs", "Fifteen Dogs - Andre Alexis.epub"),
            ("h_read2", "Pastoral", "Pastoral - Andre Alexis.epub"),
            ("h_unread", "Never Opened", "Never Opened.epub"),
        ],
    )
    conn.commit()
    conn.close()


def _seed_koreader(path: str) -> None:
    """progress_updates referencing the two read hashes only."""
    conn = sqlite3.connect(path)
    conn.execute("""
        CREATE TABLE progress_updates (
            id INTEGER PRIMARY KEY, document TEXT, percentage REAL
        )
    """)
    conn.executemany(
        "INSERT INTO progress_updates (document, percentage) VALUES (?,?)",
        [("h_read1", 0.85), ("h_read2", 0.02)],
    )
    conn.commit()
    conn.close()


def test_store_and_get_roundtrip(tmp_path):
    state_db = str(tmp_path / "state.db")
    book_pages._store(state_db, "Fifteen-Dogs", 175, "openlibrary")
    counts = book_pages.get_page_counts(state_db)
    assert counts == {"Fifteen-Dogs": {"total_pages": 175, "source": "openlibrary"}}


def test_store_replaces_existing(tmp_path):
    state_db = str(tmp_path / "state.db")
    book_pages._store(state_db, "Pastoral", 100, "estimate")
    book_pages._store(state_db, "Pastoral", 165, "openlibrary")
    counts = book_pages.get_page_counts(state_db)
    assert counts["Pastoral"] == {"total_pages": 165, "source": "openlibrary"}


def test_books_needing_pages_only_read_and_uncached(tmp_path):
    state_db = str(tmp_path / "state.db")
    koreader_db = str(tmp_path / "koreader.db")
    _seed_state(state_db)
    _seed_koreader(koreader_db)

    todo = book_pages._books_needing_pages(state_db, koreader_db)
    titles = {t for t, _ in todo}
    # only the two books with progress; the unread one is excluded
    assert titles == {"Fifteen-Dogs", "Pastoral"}

    # once cached, a book drops out of the todo list
    book_pages._store(state_db, "Fifteen-Dogs", 175, "openlibrary")
    todo2 = book_pages._books_needing_pages(state_db, koreader_db)
    assert {t for t, _ in todo2} == {"Pastoral"}


def test_books_needing_pages_no_progress(tmp_path):
    state_db = str(tmp_path / "state.db")
    koreader_db = str(tmp_path / "koreader.db")
    _seed_state(state_db)
    # koreader.db without a progress_updates table -> nothing to do
    sqlite3.connect(koreader_db).close()
    assert book_pages._books_needing_pages(state_db, koreader_db) == []


def _online() -> bool:
    req = urllib.request.Request(
        "https://openlibrary.org/search.json?q=test&limit=1",
        headers={"User-Agent": "xteink-commonplace/1.0"},
    )
    try:
        urllib.request.urlopen(req, timeout=10)
        return True
    except urllib.error.HTTPError:
        return True  # server reachable, just a non-200 status
    except Exception:
        return False


@pytest.mark.skipif(not _online(), reason="Open Library unreachable")
def test_live_lookup_fifteen_dogs():
    pages_n = book_pages.lookup_openlibrary_pages("Fifteen Dogs - Andre Alexis")
    assert pages_n is not None
    # print edition is ~171 pages; median across editions should be in range
    assert 100 <= pages_n <= 300
    print(f"\nOpen Library: Fifteen Dogs -> {pages_n} pages")


@pytest.mark.skipif(not _online(), reason="Open Library unreachable")
def test_live_lookup_no_match_returns_none():
    pages_n = book_pages.lookup_openlibrary_pages(
        "zzqx nonexistent title 9f8a7b6c no such book"
    )
    assert pages_n is None
