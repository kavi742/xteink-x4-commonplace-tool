"""Tests for the FTS5 search index.

Each test gets its own hand-built database so the index starts clean, following
the fixture style of test_api.py. ``writing_entries`` / ``writing_refs`` are
built here on purpose: the service does not create them (that is Plan 2's
work), but the writing kind has to be proved now.
"""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from xteink_service import search_index
from xteink_service.koreader_sync import app

_SOURCE_TABLES = (
    """
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
    """,
    """
    CREATE TABLE highlights (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        screenshot_id INTEGER NOT NULL, selected_text TEXT NOT NULL,
        bbox_json TEXT DEFAULT '[]', img_w INTEGER DEFAULT 0, img_h INTEGER DEFAULT 0,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )
    """,
    """
    CREATE TABLE document_aliases (
        hash TEXT PRIMARY KEY, title TEXT NOT NULL,
        filename TEXT DEFAULT '', resolved_by TEXT DEFAULT 'manual',
        computed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        kind TEXT DEFAULT 'book'
    )
    """,
    """
    CREATE TABLE clippings (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        revision INTEGER NOT NULL, document TEXT NOT NULL, clip_id TEXT NOT NULL,
        deleted INTEGER NOT NULL DEFAULT 0, spine INTEGER NOT NULL DEFAULT 0,
        start_page INTEGER NOT NULL DEFAULT 0, end_page INTEGER NOT NULL DEFAULT 0,
        pages INTEGER NOT NULL DEFAULT 1, start_word INTEGER NOT NULL DEFAULT 0,
        end_word INTEGER NOT NULL DEFAULT 0, words INTEGER NOT NULL DEFAULT 0,
        para INTEGER, chapter TEXT NOT NULL DEFAULT '', text TEXT NOT NULL DEFAULT '',
        created_at INTEGER NOT NULL DEFAULT 0, layout_signature INTEGER NOT NULL DEFAULT 0,
        start_offset INTEGER, end_offset INTEGER,
        synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(document, clip_id)
    )
    """,
    """
    CREATE TABLE writing_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        book_slug TEXT NOT NULL DEFAULT '', entry_date TEXT NOT NULL DEFAULT '',
        title TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '',
        deleted_at TIMESTAMP
    )
    """,
    """
    CREATE TABLE writing_refs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entry_id INTEGER NOT NULL, snapshot TEXT NOT NULL DEFAULT '',
        source_state TEXT NOT NULL DEFAULT 'live'
    )
    """,
)


@pytest.fixture
def db(tmp_path, monkeypatch):
    path = str(tmp_path / "state.db")
    monkeypatch.setenv("STATE_DB", path)
    with sqlite3.connect(path) as conn:
        for sql in _SOURCE_TABLES:
            conn.execute(sql)
    return path


@pytest.fixture
def client(db):
    return TestClient(app)


def _exec(db: str, sql: str, params: tuple = ()) -> int:
    with sqlite3.connect(db) as conn:
        return conn.execute(sql, params).lastrowid


def _seed_screenshot(db, book="Pastoral", ocr="", notes=None, date="2026-07-04", n=1) -> int:
    return _exec(
        db,
        "INSERT INTO synced_screenshots "
        "(device_path, content_hash, book_title, sync_date, ocr_text, user_notes) "
        "VALUES (?,?,?,?,?,?)",
        (f"/screenshots/{book}/{n}.bmp", f"hash-{book}-{n}", book, date, ocr, notes),
    )


def _seed_highlight(db, screenshot_id, text) -> int:
    return _exec(
        db,
        "INSERT INTO highlights (screenshot_id, selected_text) VALUES (?,?)",
        (screenshot_id, text),
    )


def _seed_clipping(db, text, title="Pastoral", doc="aaaa1111", clip_id="c1") -> int:
    _exec(
        db,
        "INSERT OR IGNORE INTO document_aliases (hash, title) VALUES (?,?)",
        (doc, title),
    )
    return _exec(
        db,
        "INSERT INTO clippings (revision, document, clip_id, text, created_at) "
        "VALUES (?,?,?,?,?)",
        (1, doc, clip_id, text, 1793000000),
    )


def _seed_entry(db, body, title="On attention", slug="pastoral", date="2026-07-05") -> int:
    return _exec(
        db,
        "INSERT INTO writing_entries (book_slug, entry_date, title, body) VALUES (?,?,?,?)",
        (slug, date, title, body),
    )


def _seed_ref(db, entry_id, snapshot) -> int:
    return _exec(
        db,
        "INSERT INTO writing_refs (entry_id, snapshot) VALUES (?,?)",
        (entry_id, snapshot),
    )


def _body(db: str, kind: str, ref_id: int) -> str:
    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT body FROM search_docs WHERE kind = ? AND ref_id = ?", (kind, ref_id)
        ).fetchone()
    return row[0] if row else ""


def _kinds(result: dict) -> list[str]:
    return [hit["kind"] for hit in result["results"]]


# ------------------------------------------------------------------ #
# Schema                                                               #
# ------------------------------------------------------------------ #

def test_fts5_module_is_available():
    conn = sqlite3.connect(":memory:")
    conn.execute(
        "CREATE VIRTUAL TABLE t USING fts5(a, b, tokenize='unicode61 remove_diacritics 2')"
    )
    conn.execute("INSERT INTO t(a, b) VALUES ('Pastoral', 'the pastoral mode')")
    row = conn.execute(
        "SELECT bm25(t, 10.0, 1.0), snippet(t, 1, '<mark>', '</mark>', '...', 8) "
        "FROM t WHERE t MATCH 'pastoral'"
    ).fetchone()
    assert row[0] < 0
    assert "<mark>pastoral</mark>" in row[1]


def test_init_db_is_idempotent(db):
    with sqlite3.connect(db) as conn:
        search_index.init_db(conn)
        search_index.init_db(conn)


def test_search_db_path_is_not_captured_at_import(tmp_path, monkeypatch):
    first = str(tmp_path / "first.db")
    monkeypatch.setenv("STATE_DB", first)
    assert search_index._state_db() == first
    second = str(tmp_path / "second.db")
    monkeypatch.setenv("STATE_DB", second)
    assert search_index._state_db() == second


# ------------------------------------------------------------------ #
# Rebuild                                                              #
# ------------------------------------------------------------------ #

def test_rebuild_indexes_all_five_kinds(db):
    shot = _seed_screenshot(db, ocr="the pastoral mode of attention", notes="worth revisiting")
    _seed_highlight(db, shot, "a line worth keeping")
    _seed_clipping(db, "an excerpt saved on the device")
    entry = _seed_entry(db, "The line that stayed with me: ((ref:1))")
    _seed_ref(db, entry, "a snapshot of the cited passage")

    result = search_index.rebuild(db)
    assert result["indexed"] == {
        "screenshot": 1, "note": 1, "highlight": 1, "clipping": 1, "writing": 1
    }
    assert result["skipped"] == []


def test_rebuild_is_idempotent(db):
    shot = _seed_screenshot(db, ocr="the pastoral mode", notes="a note")
    _seed_highlight(db, shot, "a line worth keeping")
    _seed_clipping(db, "an excerpt")
    entry = _seed_entry(db, "citing ((ref:1))")
    _seed_ref(db, entry, "a snapshot")

    first = search_index.rebuild(db)
    with sqlite3.connect(db) as conn:
        first_rows = conn.execute("SELECT COUNT(*) FROM search_docs").fetchone()[0]
    second = search_index.rebuild(db)
    with sqlite3.connect(db) as conn:
        second_rows = conn.execute("SELECT COUNT(*) FROM search_docs").fetchone()[0]

    assert first["indexed"] == second["indexed"]
    assert first_rows == second_rows == 5


def test_rebuild_skips_missing_tables(db):
    shot = _seed_screenshot(db, ocr="the pastoral mode", notes="a note")
    _seed_highlight(db, shot, "a line worth keeping")
    _exec(db, "DROP TABLE clippings")

    result = search_index.rebuild(db)
    assert result["skipped"] == ["clipping"]
    assert result["indexed"]["screenshot"] == 1
    assert result["indexed"]["note"] == 1
    assert result["indexed"]["highlight"] == 1


def test_ensure_built_only_builds_an_empty_index(db):
    _seed_screenshot(db, ocr="the pastoral mode")
    assert search_index.ensure_built(db)["indexed"]["screenshot"] == 1
    assert search_index.ensure_built(db) is None


# ------------------------------------------------------------------ #
# Ranking, snippets, facets                                            #
# ------------------------------------------------------------------ #

def test_title_outranks_body(db):
    _seed_screenshot(db, book="Pastoral", ocr="a page about nothing in particular", n=1)
    _seed_screenshot(db, book="Meadows", ocr="he wrote in the pastoral mode", n=2)
    search_index.rebuild(db)

    hits = search_index.search(db, "pastoral")["results"]
    assert [h["book_title"] for h in hits] == ["Pastoral", "Meadows"]
    assert hits[0]["score"] < 0


def test_snippet_marks_the_match(db):
    _seed_screenshot(db, ocr="he wrote in the pastoral mode of attention")
    search_index.rebuild(db)

    hit = search_index.search(db, "pastoral")["results"][0]
    assert "<mark>pastoral</mark>" in hit["snippet"]


def test_snippet_escapes_html(db):
    _seed_screenshot(db, ocr="plain text", notes="a note about <script>alert(1)</script>")
    search_index.rebuild(db)

    hit = search_index.search(db, "alert")["results"][0]
    assert hit["kind"] == "note"
    assert "&lt;script&gt;" in hit["snippet"]
    assert "<script>" not in hit["snippet"]
    assert "<mark>alert</mark>" in hit["snippet"]


def test_facets_count_all_kinds_regardless_of_filter(db):
    shot = _seed_screenshot(db, ocr="the pastoral mode", notes="pastoral again")
    _seed_highlight(db, shot, "pastoral once more")
    _seed_clipping(db, "pastoral in a clipping")
    search_index.rebuild(db)

    everything = search_index.search(db, "pastoral")
    filtered = search_index.search(db, "pastoral", kinds=["highlight"])

    assert filtered["facets"] == everything["facets"]
    assert filtered["facets"] == {
        "screenshot": 1, "note": 1, "highlight": 1, "clipping": 1, "writing": 0
    }
    assert filtered["total"] == everything["total"] == 4
    assert _kinds(filtered) == ["highlight"]


def test_book_filter_scopes_results(db):
    _seed_screenshot(db, book="Pastoral", ocr="a word about attention", n=1)
    _seed_screenshot(db, book="Meadows", ocr="another word about attention", n=2)
    search_index.rebuild(db)

    scoped = search_index.search(db, "attention", book="meadows")
    assert [h["book_title"] for h in scoped["results"]] == ["Meadows"]
    assert scoped["facets"]["screenshot"] == 1


@pytest.mark.parametrize("raw", ['"', "*", "^foo", "NEAR(a b)", "a AND", "!!!", "  "])
def test_to_match_query(raw, db):
    match = search_index.to_match_query(raw)
    if raw in ('"', "*", "!!!", "  "):
        assert match is None
        return
    _seed_screenshot(db, ocr="a page of prose")
    search_index.rebuild(db)
    with sqlite3.connect(db) as conn:
        conn.execute("SELECT rowid FROM search_index WHERE search_index MATCH ?", (match,))


# ------------------------------------------------------------------ #
# Index maintenance through the API                                    #
# ------------------------------------------------------------------ #

@pytest.mark.parametrize("raw", ['"', "*", "^foo", "NEAR(a b)", "a AND", "!!!", "  "])
def test_malformed_query_returns_empty_not_500(raw, client, db):
    _seed_screenshot(db, ocr="a page of prose")
    search_index.rebuild(db)

    res = client.get("/api/search", params={"q": raw})
    assert res.status_code == 200
    assert res.json()["results"] == []


def test_update_screenshot_reindexes(client, db):
    shot = _seed_screenshot(db, ocr="the orignal misread text")
    search_index.rebuild(db)
    assert search_index.search(db, "orignal")["results"]

    res = client.put(f"/api/screenshots/{shot}", json={"ocr_corrected": "the original text"})
    assert res.status_code == 200
    assert search_index.search(db, "orignal")["results"] == []
    assert search_index.search(db, "original")["results"][0]["ref_id"] == shot


def test_delete_clipping_removes_from_index(client, db):
    clip = _seed_clipping(db, "an excerpt about attention")
    search_index.rebuild(db)
    assert search_index.search(db, "attention")["results"]

    assert client.delete(f"/api/clippings/{clip}").status_code == 200
    assert search_index.search(db, "attention")["results"] == []


def test_reindex_endpoint_is_repeatable(client, db):
    _seed_screenshot(db, ocr="the pastoral mode")
    first = client.post("/api/search/reindex").json()
    second = client.post("/api/search/reindex").json()
    assert first["indexed"] == second["indexed"]
    assert "duration_ms" in first


def test_notes_only_lists_every_note_without_a_query(client, db):
    shot = _seed_screenshot(db, ocr="a page of prose", notes="worth revisiting", n=1)
    _seed_screenshot(db, ocr="another page", n=2)
    search_index.rebuild(db)

    body = client.get("/api/search", params={"notes_only": "1"}).json()
    assert [(h["kind"], h["ref_id"]) for h in body["results"]] == [("note", shot)]
    assert body["total"] == 1


def test_empty_query_without_a_kind_filter_returns_nothing(client, db):
    _seed_screenshot(db, ocr="a page of prose")
    search_index.rebuild(db)

    body = client.get("/api/search", params={"q": ""}).json()
    assert body["results"] == []
    assert body["facets"] == {
        "screenshot": 0, "note": 0, "highlight": 0, "clipping": 0, "writing": 0
    }


# ------------------------------------------------------------------ #
# Writing entries                                                      #
# ------------------------------------------------------------------ #

def test_search_finds_a_phrase_that_exists_only_in_a_snapshot(db):
    entry = _seed_entry(db, "The line that stayed with me: ((ref:1))")
    _seed_ref(db, entry, "the pastoral mode of attention")
    search_index.rebuild(db)

    hits = search_index.search(db, "pastoral mode of attention")["results"]
    assert [h["kind"] for h in hits] == ["writing"]
    assert hits[0]["ref_id"] == entry


def test_search_finds_both_the_clipping_and_the_entry_that_cites_it(db):
    phrase = "the pastoral mode of attention"
    entry = _seed_entry(db, "The line that stayed with me: ((ref:1))")
    _seed_ref(db, entry, phrase)
    _seed_clipping(db, phrase)
    search_index.rebuild(db)

    result = search_index.search(db, phrase)
    assert sorted(_kinds(result)) == ["clipping", "writing"]
    assert result["facets"]["clipping"] == 1
    assert result["facets"]["writing"] == 1


def test_anchor_token_is_not_indexed(db):
    entry = _seed_entry(db, "The line that stayed with me: ((ref:1))")
    _seed_ref(db, entry, "a snapshot of the cited passage")
    search_index.rebuild(db)

    assert "((ref:" not in _body(db, "writing", entry)
    assert search_index.search(db, "ref")["results"] == []


def test_unresolvable_anchor_is_dropped(db):
    entry = _seed_entry(db, "Nothing resolves here: ((ref:99))")
    search_index.rebuild(db)

    body = _body(db, "writing", entry)
    assert body
    assert "((ref:" not in body


def test_reindex_after_a_snapshot_changes(db):
    entry = _seed_entry(db, "The line that stayed with me: ((ref:1))")
    ref = _seed_ref(db, entry, "the pastoral mode of attention")
    search_index.rebuild(db)

    _exec(db, "UPDATE writing_refs SET snapshot = ? WHERE id = ?",
          ("the georgic habit of labour", ref))
    with sqlite3.connect(db) as conn:
        search_index.index_writing_for_ref(conn, ref)

    assert search_index.search(db, "pastoral")["results"] == []
    assert search_index.search(db, "georgic")["results"][0]["ref_id"] == entry


def test_source_state_flip_does_not_change_the_indexed_body(db):
    entry = _seed_entry(db, "The line that stayed with me: ((ref:1))")
    ref = _seed_ref(db, entry, "the pastoral mode of attention")
    search_index.rebuild(db)
    before = _body(db, "writing", entry)

    _exec(db, "UPDATE writing_refs SET source_state = 'deleted' WHERE id = ?", (ref,))
    assert _body(db, "writing", entry) == before


def test_entry_title_does_not_outrank_a_body_match(db):
    _seed_entry(db, "The line that stayed with me: ((ref:1))", title="Attention")
    _seed_clipping(db, "attention, attention, attention: the whole of attention")
    search_index.rebuild(db)

    assert _kinds(search_index.search(db, "attention")) == ["clipping", "writing"]


def test_writing_entry_keeps_the_books_title(db):
    _seed_screenshot(db, book="Pastoral", ocr="a page")
    entry = _seed_entry(db, "A thought", slug="pastoral")
    search_index.rebuild(db)

    hit = search_index.search(db, "thought")["results"][0]
    assert hit["ref_id"] == entry
    assert hit["book_title"] == "Pastoral"
    assert hit["book_slug"] == "pastoral"


def test_soft_deleted_entry_leaves_the_index(db):
    entry = _seed_entry(db, "The line that stayed with me: ((ref:1))")
    _seed_ref(db, entry, "the pastoral mode of attention")
    search_index.rebuild(db)

    _exec(db, "UPDATE writing_entries SET deleted_at = '2026-11-08' WHERE id = ?", (entry,))
    with sqlite3.connect(db) as conn:
        search_index.index_writing(conn, entry)
    assert search_index.search(db, "pastoral")["results"] == []

    _exec(db, "UPDATE writing_entries SET deleted_at = NULL WHERE id = ?", (entry,))
    with sqlite3.connect(db) as conn:
        search_index.index_writing(conn, entry)
    assert search_index.search(db, "pastoral")["results"][0]["ref_id"] == entry


def test_writing_kind_appears_in_facets_zero_filled(client, db):
    _exec(db, "DROP TABLE writing_refs")
    _exec(db, "DROP TABLE writing_entries")
    _seed_screenshot(db, ocr="the pastoral mode")

    assert client.post("/api/search/reindex").json()["skipped"] == ["writing"]
    assert search_index.search(db, "pastoral")["facets"]["writing"] == 0


@pytest.mark.parametrize("body,expected", [
    ("", ""),
    ("no anchors here", "no anchors here"),
    ("((ref:1)) at the start", "SNAP at the start"),
    ("at the end ((ref:1))", "at the end SNAP"),
    ("((ref:1))((ref:2))", "SNAPOTHER"),
    ("((ref:1)) and ((ref:1))", "SNAP and SNAP"),
    ("gone ((ref:9))", "gone "),
])
def test_expand_anchors_is_pure(body, expected):
    snapshots = {1: "SNAP", 2: "OTHER"}
    assert search_index.expand_anchors(body, snapshots) == expected
    assert snapshots == {1: "SNAP", 2: "OTHER"}
