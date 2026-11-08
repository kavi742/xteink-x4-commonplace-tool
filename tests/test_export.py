"""Export: `GET /api/books/{slug}/export` and the pure renderers behind it.

Two contracts dominate. Nothing the user deleted may appear in an export, in
any format. And an exported Markdown file has to stand on its own, so an
unexpanded `((ref:` token is a bug rather than a detail.
"""
import csv
import io
import sqlite3

import pytest
from fastapi.testclient import TestClient

from xteink_service import clippings, export
from xteink_service import koreader_sync as ks
from xteink_service.koreader_sync import app
from xteink_service.state import SyncState

BOOK = "Pastoral"
SLUG = "pastoral"
DOC = "eeeeeeeeeeeeeeeeeeeeeeeeeeeeeee1"

_WRITING_TABLES = (
    """
    CREATE TABLE writing_entries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        book_slug TEXT NOT NULL DEFAULT '', entry_date TEXT NOT NULL DEFAULT '',
        title TEXT NOT NULL DEFAULT '', body TEXT NOT NULL DEFAULT '',
        position_pct REAL, deleted_at TIMESTAMP
    )
    """,
    """
    CREATE TABLE writing_refs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        entry_id INTEGER NOT NULL, kind TEXT NOT NULL DEFAULT '',
        ref_id INTEGER NOT NULL DEFAULT 0, document TEXT,
        snapshot TEXT NOT NULL DEFAULT '', image_path TEXT,
        position TEXT NOT NULL DEFAULT '', source_state TEXT NOT NULL DEFAULT 'live'
    )
    """,
)


# ------------------------------------------------------------------ #
# Fixtures                                                             #
# ------------------------------------------------------------------ #

@pytest.fixture
def env(tmp_path, monkeypatch):
    state_db = str(tmp_path / "state.db")
    vault = tmp_path / "vault"
    (vault / "Books").mkdir(parents=True)
    monkeypatch.setenv("STATE_DB", state_db)
    monkeypatch.setenv("KOREADER_DB", str(tmp_path / "koreader.db"))
    monkeypatch.setenv("VAULT_PATH", str(vault))
    SyncState(state_db)
    clippings.init_db()
    return state_db


@pytest.fixture
def client(env):
    return TestClient(app)


def _exec(db: str, sql: str, params: tuple = ()) -> int:
    with sqlite3.connect(db) as conn:
        return conn.execute(sql, params).lastrowid


def _screenshot(db: str, book: str = BOOK, date: str = "2026-07-04",
                ocr: str = "OCR text", note: str | None = None) -> int:
    return _exec(
        db,
        "INSERT INTO synced_screenshots "
        "(device_path, content_hash, book_title, sync_date, ocr_text, user_notes) "
        "VALUES (?,?,?,?,?,?)",
        (f"/screenshots/{book}/{date}-{ocr[:8]}.bmp", f"h-{book}-{date}-{ocr[:8]}",
         book, date, ocr, note),
    )


def _highlight(db: str, screenshot_id: int, text: str,
               created: str = "2026-07-04 10:00:00", deleted: str | None = None) -> int:
    return _exec(
        db,
        "INSERT INTO highlights (screenshot_id, selected_text, created_at, deleted_at) "
        "VALUES (?,?,?,?)",
        (screenshot_id, text, created, deleted),
    )


def _alias(db: str, doc_hash: str = DOC, title: str = BOOK) -> None:
    _exec(db, "INSERT INTO document_aliases (hash, title) VALUES (?,?)", (doc_hash, title))


def _clipping(db: str, clip_id: str, text: str, chapter: str = "",
              created: int = 1751587200, doc: str = DOC, deleted: int = 0,
              spine: int = 0, start_page: int = 10) -> int:
    return _exec(
        db,
        "INSERT INTO clippings (revision, document, clip_id, deleted, spine, start_page, "
        "end_page, pages, chapter, text, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
        (1, doc, clip_id, deleted, spine, start_page, start_page, 1, chapter, text, created),
    )


def _writing_tables(db: str) -> None:
    with sqlite3.connect(db) as conn:
        for sql in _WRITING_TABLES:
            conn.execute(sql)


def _entry(db: str, title: str, body: str, slug: str = SLUG,
           entry_date: str = "2026-11-07", pct: float = 41.5) -> int:
    return _exec(
        db,
        "INSERT INTO writing_entries (book_slug, entry_date, title, body, position_pct) "
        "VALUES (?,?,?,?,?)",
        (slug, entry_date, title, body, pct),
    )


def _ref(db: str, entry_id: int, kind: str, snapshot: str, position: str = "",
         image_path: str | None = None, state: str = "live",
         document: str | None = None) -> int:
    return _exec(
        db,
        "INSERT INTO writing_refs (entry_id, kind, ref_id, document, snapshot, "
        "image_path, position, source_state) VALUES (?,?,?,?,?,?,?,?)",
        (entry_id, kind, 1, document, snapshot, image_path, position, state),
    )


def _seed_book(db: str) -> int:
    """One screenshot, one highlight, one alias, one chaptered clipping."""
    shot = _screenshot(db)
    _highlight(db, shot, "A sentence worth keeping.")
    _alias(db)
    _clipping(db, "cp-1", "An excerpt saved on the reader.", chapter="Chapter One")
    return shot


def _md(db: str, slug: str = SLUG, include=export.INCLUDES, group: str = "auto") -> str:
    return export.build(db, slug, include, "md", group)[0]


def _json(db: str, slug: str = SLUG, include=export.INCLUDES, group: str = "auto") -> dict:
    return export.build(db, slug, include, "json", group)[0]


def _csv_rows(db: str, slug: str = SLUG, include=export.INCLUDES) -> list[list[str]]:
    body = export.build(db, slug, include, "csv")[0]
    return list(csv.reader(io.StringIO(body)))


# ------------------------------------------------------------------ #
# Formats                                                             #
# ------------------------------------------------------------------ #

def test_all_three_formats_carry_clippings_and_highlights(env):
    _seed_book(env)

    md = _md(env)
    assert "A sentence worth keeping." in md
    assert "An excerpt saved on the reader." in md
    assert "`highlight`" in md and "`clipping` ch. page 11 of 1" in md

    payload = _json(env)
    texts = [i["text"] for g in payload["groups"] for i in g["items"]]
    assert "A sentence worth keeping." in texts
    assert "An excerpt saved on the reader." in texts
    assert payload["counts"] == {"highlights": 1, "clippings": 1, "notes": 0, "writing": 0}

    rows = _csv_rows(env)
    assert rows[0] == list(export.CSV_HEADER)
    assert {r[0] for r in rows[1:]} == {"highlight", "clipping"}


def test_json_shape(env):
    _seed_book(env)
    payload = _json(env)

    assert set(payload) == {
        "book_slug", "book_title", "generated_at", "include",
        "grouped_by", "counts", "groups",
    }
    item = payload["groups"][0]["items"][0]
    for key in ("kind", "id", "text", "chapter", "date", "page_label", "screenshot_id"):
        assert key in item


def test_csv_quotes_multiline_text(env):
    _alias(env)
    messy = 'A line\nand a "quoted" one'
    _clipping(env, "cp-messy", messy, chapter="Chapter One")

    rows = _csv_rows(env)
    assert rows[1][export.CSV_HEADER.index("text")] == messy


def test_media_type_and_filename_per_format(env):
    _seed_book(env)
    for fmt, media in export.MEDIA_TYPES.items():
        _, media_type, filename = export.build(env, SLUG, export.INCLUDES, fmt)
        assert media_type == media
        assert filename == f"pastoral.{fmt}"


# ------------------------------------------------------------------ #
# Grouping                                                            #
# ------------------------------------------------------------------ #

def test_md_groups_by_chapter_when_clippings_have_chapters(env):
    _alias(env)
    _clipping(env, "cp-1", "First.", chapter="Chapter One", start_page=1)
    _clipping(env, "cp-2", "Second.", chapter="Chapter Two", start_page=9)

    headings = [line for line in _md(env).splitlines() if line.startswith("## ")]
    assert headings == ["## Chapter One", "## Chapter Two"]


def test_md_groups_by_date_when_no_chapters(env):
    _alias(env)
    _clipping(env, "cp-1", "Older.", created=1751587200)   # 2025-07-04
    _clipping(env, "cp-2", "Newer.", created=1783123200, start_page=11)  # 2026-07-04

    headings = [line for line in _md(env).splitlines() if line.startswith("## ")]
    assert headings == ["## 2026-07-04", "## 2025-07-04"]


def test_group_auto_prefers_chapter_when_any_clipping_has_one(env):
    _alias(env)
    _clipping(env, "cp-1", "Unchaptered one.", chapter="", start_page=1)
    _clipping(env, "cp-2", "Chaptered one.", chapter="Chapter Two", start_page=9)

    assert _json(env)["grouped_by"] == "chapter"


def test_group_date_can_be_forced_over_chapters(env):
    _alias(env)
    _clipping(env, "cp-1", "Chaptered.", chapter="Chapter One")

    payload = _json(env, group="date")
    assert payload["grouped_by"] == "date"
    assert payload["groups"][0]["label"] == "2025-07-04"


def test_chapterless_passages_land_in_unchaptered_last(env):
    _seed_book(env)
    labels = [g["label"] for g in _json(env)["groups"]]
    assert labels == ["Chapter One", "Unchaptered"]


# ------------------------------------------------------------------ #
# Soft delete                                                         #
# ------------------------------------------------------------------ #

def test_soft_deleted_highlight_is_absent_from_every_format(env):
    shot = _screenshot(env)
    _highlight(env, shot, "Still here.")
    _highlight(env, shot, "Gone for good.", deleted="2026-11-07 09:00:00")

    assert "Gone for good." not in _md(env)
    assert "Gone for good." not in export.build(env, SLUG, export.INCLUDES, "csv")[0]
    texts = [i["text"] for g in _json(env)["groups"] for i in g["items"]]
    assert texts == ["Still here."]


def test_tombstoned_clipping_is_absent_from_every_format(env):
    _alias(env)
    _clipping(env, "cp-live", "Still here.", chapter="Chapter One", start_page=1)
    # A tombstone clears `text` into `trashed_text`; both halves must be invisible.
    gone = _clipping(env, "cp-gone", "", chapter="Chapter One", deleted=1, start_page=2)
    _exec(env, "UPDATE clippings SET trashed_text = ? WHERE id = ?", ("Gone for good.", gone))

    assert "Gone for good." not in _md(env)
    assert "Gone for good." not in export.build(env, SLUG, export.INCLUDES, "csv")[0]
    texts = [i["text"] for g in _json(env)["groups"] for i in g["items"]]
    assert texts == ["Still here."]
    assert _json(env)["counts"]["clippings"] == 1


# ------------------------------------------------------------------ #
# include / empty / slug                                              #
# ------------------------------------------------------------------ #

def test_include_filters_sections(env):
    _seed_book(env)
    payload = _json(env, include={"highlights"})

    assert payload["counts"]["clippings"] == 0
    assert payload["include"] == ["highlights"]
    assert "An excerpt saved on the reader." not in _md(env, include={"highlights"})


def test_notes_ride_along_with_their_screenshot(env):
    shot = _screenshot(env, note="Worth coming back to.")
    _highlight(env, shot, "A sentence worth keeping.")

    assert "Note: Worth coming back to." in _md(env)
    assert _json(env)["counts"]["notes"] == 1


def test_a_note_without_a_highlight_is_still_exported(env):
    _screenshot(env, note="An orphan note.")
    assert "Note: An orphan note." in _md(env)


def test_book_with_no_passages_exports_an_empty_but_valid_document(env):
    _screenshot(env, ocr="")  # names the book, carries nothing exportable

    md = _md(env)
    assert md.startswith(f"# {BOOK}")
    assert "0 highlights, 0 clippings, 0 notes, 0 writing entries" in md

    payload = _json(env)
    assert payload["groups"] == []
    assert payload["counts"] == {"highlights": 0, "clippings": 0, "notes": 0, "writing": 0}

    rows = _csv_rows(env)
    assert rows == [list(export.CSV_HEADER)]


def test_unknown_book_raises_lookup_error(env):
    with pytest.raises(LookupError):
        export.build(env, "no-such-book", export.INCLUDES, "md")


def test_slug_matches_both_title_forms(env):
    shot_id = _screenshot(env, book="The-Hidden-Keys", ocr="From the screenshot side")
    _highlight(env, shot_id, "From the screenshot side.")
    _alias(env, "hhhh1111", "Hidden Keys, The - Andre Alexis")
    _clipping(env, "cp-hk", "From the alias side.", chapter="Chapter One", doc="hhhh1111")

    md = _md(env, "the-hidden-keys")
    assert "From the screenshot side." in md
    assert "From the alias side." in md


# ------------------------------------------------------------------ #
# Filenames                                                           #
# ------------------------------------------------------------------ #

@pytest.mark.parametrize("raw, expected", [
    ("The/Hidden/Keys", "The-Hidden-Keys.md"),
    ('A "quoted" title', "A-quoted-title.md"),
    ("../../etc/passwd", "etc-passwd.md"),
    ("", "export.md"),
    ("///", "export.md"),
])
def test_filename_is_sanitised(raw, expected):
    assert export.safe_filename(raw, "md") == expected


# ------------------------------------------------------------------ #
# Writing entries (the tables land with PLAN-2 item 2.5)              #
# ------------------------------------------------------------------ #

def test_export_degrades_when_the_writing_tables_are_missing(env, client):
    _seed_book(env)
    res = client.get(f"/api/books/{SLUG}/export?format=json")
    assert res.status_code == 200
    assert res.json()["counts"]["writing"] == 0


def test_md_entry_contains_no_anchor_token(env):
    _writing_tables(env)
    _seed_book(env)
    entry = _entry(env, "On attention", "What stayed with me:\n\n((ref:1))\n\nand ((ref:2)).\n")
    _ref(env, entry, "clipping", "A sentence worth keeping.", position="ch. page 11 of 1")
    _ref(env, entry, "screenshot", "Apple. Now, because Bigland...",
         image_path="Pastoral/2026-07-04-01.png")

    md = _md(env)
    assert "((ref:" not in md
    assert "> A sentence worth keeping." in md
    assert "`cited clipping` ch. page 11 of 1" in md
    assert "![[Pastoral/2026-07-04-01.png]]" in md


def test_unresolvable_anchor_is_dropped_from_every_format(env):
    _writing_tables(env)
    _seed_book(env)
    _entry(env, "On attention", "Nothing resolves here: ((ref:999)).\n")

    assert "((ref:" not in _md(env)
    assert "((ref:" not in export.build(env, SLUG, export.INCLUDES, "csv")[0]
    entry = [i for g in _json(env)["groups"] for i in g["items"] if i["kind"] == "writing"][0]
    assert "((ref:999))" in entry["body"]
    assert "((ref:" not in entry["body_expanded"]


def test_deleted_and_drifted_refs_still_export_their_snapshot(env):
    _writing_tables(env)
    _seed_book(env)
    entry = _entry(env, "On attention", "((ref:1))\n\n((ref:2))\n")
    _ref(env, entry, "clipping", "Quoted from a deleted source.", state="deleted")
    _ref(env, entry, "clipping", "Quoted from a changed source.", state="drifted")

    md = _md(env)
    assert "Quoted from a deleted source." in md
    assert "`cited clipping` - source deleted" in md
    assert "`cited clipping` - source changed since citation" in md


def test_chapter_mode_puts_writing_first_not_unchaptered(env):
    _writing_tables(env)
    _seed_book(env)
    _entry(env, "On attention", "A thought.\n")

    payload = _json(env, group="chapter")
    assert payload["groups"][0]["label"] == "Writing"
    unchaptered = [g for g in payload["groups"] if g["label"] == "Unchaptered"][0]
    assert all(i["kind"] != "writing" for i in unchaptered["items"])


def test_date_mode_groups_an_entry_under_its_entry_date(env):
    _writing_tables(env)
    _seed_book(env)
    _entry(env, "On attention", "A thought.\n", entry_date="2026-11-07")

    labels = [g["label"] for g in _json(env, group="date")["groups"]]
    assert labels[0] == "2026-11-07"


def test_json_carries_both_body_and_body_expanded_with_refs_in_order(env):
    _writing_tables(env)
    _seed_book(env)
    entry = _entry(env, "On attention", "Second ((ref:2)) then first ((ref:1)).\n")
    _ref(env, entry, "clipping", "ONE")
    _ref(env, entry, "clipping", "TWO")

    item = [i for g in _json(env)["groups"] for i in g["items"] if i["kind"] == "writing"][0]
    assert "((ref:2))" in item["body"]
    assert "((ref:" not in item["body_expanded"]
    assert item["body_expanded"] == "Second TWO then first ONE.\n"
    assert [r["id"] for r in item["refs"]] == [2, 1]


def test_csv_flattens_an_entry_to_one_row(env):
    _writing_tables(env)
    _writing = _entry(env, "On attention", "What stayed with me: ((ref:1))\n")
    _ref(env, _writing, "clipping", "A sentence worth keeping.")
    _seed_book(env)

    rows = _csv_rows(env)
    writing_rows = [r for r in rows[1:] if r[0] == "writing"]
    assert len(writing_rows) == 1
    text = writing_rows[0][export.CSV_HEADER.index("text")]
    assert text == "On attention\n\nWhat stayed with me: A sentence worth keeping."
    assert writing_rows[0][export.CSV_HEADER.index("page_label")] == "42%"


def test_writing_is_in_the_default_include(env):
    _writing_tables(env)
    _seed_book(env)
    _entry(env, "On attention", "A thought.\n")

    assert "On attention" in _md(env)
    assert "On attention" not in _md(env, include={"highlights", "clippings", "notes"})


def test_soft_deleted_writing_entry_is_excluded(env):
    _writing_tables(env)
    _seed_book(env)
    entry = _entry(env, "On attention", "A thought.\n")
    _exec(env, "UPDATE writing_entries SET deleted_at = ? WHERE id = ?",
          ("2026-11-07 09:00:00", entry))

    assert "On attention" not in _md(env)
    assert _json(env)["counts"]["writing"] == 0


# ------------------------------------------------------------------ #
# HTTP                                                                #
# ------------------------------------------------------------------ #

def test_endpoint_returns_markdown_with_a_disposition(env, client):
    _seed_book(env)
    res = client.get(f"/api/books/{SLUG}/export?format=md")

    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/markdown")
    assert res.headers["content-disposition"] == 'attachment; filename="pastoral.md"'
    assert "A sentence worth keeping." in res.text


def test_endpoint_json_has_no_disposition(env, client):
    _seed_book(env)
    res = client.get(f"/api/books/{SLUG}/export?format=json")

    assert res.status_code == 200
    assert "content-disposition" not in res.headers
    assert res.json()["book_title"] == BOOK


def test_unknown_format_returns_400(env, client):
    _seed_book(env)
    assert client.get(f"/api/books/{SLUG}/export?format=pdf").status_code == 400


def test_unknown_include_returns_400(env, client):
    _seed_book(env)
    res = client.get(f"/api/books/{SLUG}/export?include=highlights,bookmarks")
    assert res.status_code == 400
    assert "bookmarks" in res.json()["detail"]


def test_unknown_group_returns_400(env, client):
    _seed_book(env)
    assert client.get(f"/api/books/{SLUG}/export?group=spine").status_code == 400


def test_unknown_book_returns_404(env, client):
    assert client.get("/api/books/no-such-book/export").status_code == 404


def test_export_is_refused_on_the_public_sync_host(env, client, monkeypatch):
    _seed_book(env)
    monkeypatch.setattr(ks, "_PUBLIC_SYNC_HOST", "sync.example.org")
    pub = {"host": "sync.example.org"}

    assert client.get(f"/api/books/{SLUG}/export", headers=pub).status_code == 404
    assert client.get(f"/api/books/{SLUG}/export").status_code == 200
