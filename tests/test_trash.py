"""Soft delete, trash, restore, and purge.

Two contracts dominate this file. A clipping row is never removed, because the
device resurrects any id it still holds. And deleting a passage must not gut
the writing that quotes it: the entry body is never touched, only the citing
ref is flagged.
"""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from xteink_service import clippings
from xteink_service.koreader_sync import app
from xteink_service.state import SyncState

DOC = "ccccccccccccccccccccccccccccccc1"
SYNC_URL = f"/api/v1/clippings/{DOC}"
PASSAGE = "A sentence worth keeping."

_WRITING_TABLES = (
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
        entry_id INTEGER NOT NULL, kind TEXT NOT NULL DEFAULT '',
        ref_id INTEGER NOT NULL DEFAULT 0, snapshot TEXT NOT NULL DEFAULT '',
        source_state TEXT NOT NULL DEFAULT 'live'
    )
    """,
)


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
    return {"db": state_db, "vault": vault}


@pytest.fixture
def client(env):
    return TestClient(app)


@pytest.fixture
def writing(env):
    with sqlite3.connect(env["db"]) as conn:
        for sql in _WRITING_TABLES:
            conn.execute(sql)
    return env


def _exec(db: str, sql: str, params: tuple = ()) -> int:
    with sqlite3.connect(db) as conn:
        return conn.execute(sql, params).lastrowid


def _rows(db: str, sql: str, params: tuple = ()) -> list[tuple]:
    with sqlite3.connect(db) as conn:
        return conn.execute(sql, params).fetchall()


def _one(db: str, sql: str, params: tuple = ()):
    return _rows(db, sql, params)[0][0]


def _item(clip_id: str, text: str = PASSAGE, **over) -> dict:
    item = {
        "id": clip_id, "spine": 1, "start_page": 2, "end_page": 2, "pages": 1,
        "start_word": 0, "end_word": 5, "words": 5, "chapter": "Chapter One",
        "text": text, "created_at": 1793000000, "layout_signature": 7,
    }
    item.update(over)
    return item


def _upload(client, clip_id: str, text: str = PASSAGE) -> None:
    assert client.put(SYNC_URL, json={"items": [_item(clip_id, text)]}).status_code == 200


def _clipping_id(db: str, clip_id: str) -> int:
    return _one(db, "SELECT id FROM clippings WHERE clip_id = ?", (clip_id,))


def _seed_screenshot(db: str, book: str = "Pastoral", ocr: str = PASSAGE) -> int:
    return _exec(
        db,
        "INSERT INTO synced_screenshots "
        "(device_path, content_hash, book_title, sync_date, ocr_text, vault_png_path) "
        "VALUES (?,?,?,?,?,?)",
        (f"/screenshots/{book}.bmp", f"hash-{book}", book, "2026-07-04", ocr, "x.png"),
    )


def _seed_highlight(client, screenshot_id: int, text: str = PASSAGE) -> int:
    res = client.post(
        f"/api/screenshots/{screenshot_id}/highlights", json={"selected_text": text}
    )
    assert res.status_code == 200
    return res.json()["id"]


def _seed_citation(db: str, kind: str, ref_id: int, state: str = "live") -> tuple[int, int]:
    entry = _exec(
        db,
        "INSERT INTO writing_entries (book_slug, entry_date, title, body) VALUES (?,?,?,?)",
        ("pastoral", "2026-11-07", "On attention", "The line that stayed: ((ref:1))"),
    )
    ref = _exec(
        db,
        "INSERT INTO writing_refs (entry_id, kind, ref_id, snapshot, source_state) "
        "VALUES (?,?,?,?,?)",
        (entry, kind, ref_id, PASSAGE, state),
    )
    return entry, ref


# ------------------------------------------------------------------ #
# Highlights                                                           #
# ------------------------------------------------------------------ #

def test_delete_highlight_is_soft(client, env):
    shot = _seed_screenshot(env["db"])
    highlight_id = _seed_highlight(client, shot)
    body = client.delete(f"/api/highlights/{highlight_id}").json()
    assert body["soft"] is True
    assert _one(env["db"], "SELECT COUNT(*) FROM highlights WHERE id = ?", (highlight_id,)) == 1
    assert _one(env["db"], "SELECT deleted_at FROM highlights WHERE id = ?", (highlight_id,))


def test_deleting_twice_reports_not_found(client, env):
    shot = _seed_screenshot(env["db"])
    highlight_id = _seed_highlight(client, shot)
    assert client.delete(f"/api/highlights/{highlight_id}").status_code == 200
    assert client.delete(f"/api/highlights/{highlight_id}").status_code == 404


def test_deleted_highlight_disappears_from_all_three_list_endpoints(client, env):
    shot = _seed_screenshot(env["db"])
    highlight_id = _seed_highlight(client, shot)
    client.delete(f"/api/highlights/{highlight_id}")

    assert client.get("/api/highlights").json() == []
    assert client.get("/api/books/Pastoral/highlights").json() == []
    assert client.get(f"/api/screenshots/{shot}/highlights").json() == []


def test_restore_highlight_brings_it_back(client, env):
    shot = _seed_screenshot(env["db"])
    highlight_id = _seed_highlight(client, shot)
    client.delete(f"/api/highlights/{highlight_id}")

    res = client.post(f"/api/highlights/{highlight_id}/restore")
    assert res.status_code == 200
    assert res.json()["id"] == highlight_id
    assert [h["id"] for h in client.get("/api/highlights").json()] == [highlight_id]


def test_restore_a_live_highlight_returns_404(client, env):
    shot = _seed_screenshot(env["db"])
    highlight_id = _seed_highlight(client, shot)
    assert client.post(f"/api/highlights/{highlight_id}/restore").status_code == 404


def test_purge_requires_confirm(client, env):
    shot = _seed_screenshot(env["db"])
    highlight_id = _seed_highlight(client, shot)
    client.delete(f"/api/highlights/{highlight_id}")

    res = client.delete(f"/api/trash/highlights/{highlight_id}")
    assert res.status_code == 400
    assert res.json()["detail"] == "Pass confirm=1 to purge permanently"
    assert client.post("/api/trash/empty").status_code == 400


def test_purge_refuses_a_live_highlight(client, env):
    shot = _seed_screenshot(env["db"])
    highlight_id = _seed_highlight(client, shot)
    assert client.delete(f"/api/trash/highlights/{highlight_id}?confirm=1").status_code == 404
    assert _one(env["db"], "SELECT COUNT(*) FROM highlights") == 1


def test_purged_highlight_is_really_gone(client, env):
    shot = _seed_screenshot(env["db"])
    highlight_id = _seed_highlight(client, shot)
    client.delete(f"/api/highlights/{highlight_id}")
    body = client.delete(f"/api/trash/highlights/{highlight_id}?confirm=1").json()
    assert body["row_retained"] is False
    assert _one(env["db"], "SELECT COUNT(*) FROM highlights") == 0


# ------------------------------------------------------------------ #
# The vault mark, which is the bug this item fixes                     #
# ------------------------------------------------------------------ #

def test_delete_highlight_strips_the_vault_mark(client, env):
    note = env["vault"] / "Books" / "Pastoral.md"
    note.write_text(f"## 2026-07-04\n\n> {PASSAGE}\n")
    shot = _seed_screenshot(env["db"])
    highlight_id = _seed_highlight(client, shot)
    assert f"=={PASSAGE}==" in note.read_text()

    body = client.delete(f"/api/highlights/{highlight_id}").json()
    assert body["unmarked_vault"] is True
    assert "==" not in note.read_text()


def test_restore_highlight_rewrites_the_vault_mark(client, env):
    note = env["vault"] / "Books" / "Pastoral.md"
    note.write_text(f"## 2026-07-04\n\n> {PASSAGE}\n")
    shot = _seed_screenshot(env["db"])
    highlight_id = _seed_highlight(client, shot)
    client.delete(f"/api/highlights/{highlight_id}")

    client.post(f"/api/highlights/{highlight_id}/restore")
    assert f"=={PASSAGE}==" in note.read_text()


def test_unmark_is_a_noop_when_the_note_was_edited(client, env):
    note = env["vault"] / "Books" / "Pastoral.md"
    note.write_text(f"## 2026-07-04\n\n> {PASSAGE}\n")
    shot = _seed_screenshot(env["db"])
    highlight_id = _seed_highlight(client, shot)
    note.write_text("## 2026-07-04\n\n> Rewritten by hand in Obsidian.\n")

    body = client.delete(f"/api/highlights/{highlight_id}").json()
    assert body["unmarked_vault"] is False
    assert body["soft"] is True


def test_unmark_is_a_noop_when_the_note_is_missing(client, env):
    shot = _seed_screenshot(env["db"])
    highlight_id = _seed_highlight(client, shot)
    assert client.delete(f"/api/highlights/{highlight_id}").json()["unmarked_vault"] is False


# ------------------------------------------------------------------ #
# Clippings                                                            #
# ------------------------------------------------------------------ #

def test_delete_clipping_preserves_text_in_trashed_text(client, env):
    _upload(client, "clip-a")
    clip_id = _clipping_id(env["db"], "clip-a")
    client.delete(f"/api/clippings/{clip_id}")

    text, trashed = _rows(
        env["db"], "SELECT text, trashed_text FROM clippings WHERE id = ?", (clip_id,)
    )[0]
    assert text == ""
    assert trashed == PASSAGE


def test_clipping_wire_shape_unchanged_for_tombstone(client, env):
    """The device contract: trashed_text must never reach the wire."""
    _upload(client, "clip-a")
    client.delete(f"/api/clippings/{_clipping_id(env['db'], 'clip-a')}")
    item = client.get(SYNC_URL, params={"cursor": 0, "limit": 10}).json()["items"][0]
    assert set(item) == {"id", "deleted", "revision"}


def test_restore_clipping_takes_a_fresh_higher_revision(client, env):
    _upload(client, "clip-a")
    clip_id = _clipping_id(env["db"], "clip-a")
    deleted = client.delete(f"/api/clippings/{clip_id}").json()
    restored = client.post(f"/api/clippings/{clip_id}/restore").json()
    assert restored["revision"] > deleted["revision"]
    assert _one(env["db"], "SELECT text FROM clippings WHERE id = ?", (clip_id,)) == PASSAGE


def test_pagination_still_strictly_advances_across_a_delete_and_restore(client, env):
    _upload(client, "clip-a")
    _upload(client, "clip-b")
    clip_id = _clipping_id(env["db"], "clip-a")
    client.delete(f"/api/clippings/{clip_id}")
    client.post(f"/api/clippings/{clip_id}/restore")

    cursor, seen = 0, []
    for _ in range(10):
        body = client.get(
            SYNC_URL, params={"cursor": cursor, "limit": 1, "format": "reader"}
        ).json()
        assert len(body["items"]) <= 1
        assert body["cursor"] >= cursor
        if body["items"]:
            assert body["cursor"] > cursor
            seen.append(body["items"][0]["id"])
        else:
            assert body["cursor"] == cursor
            assert body["more"] is False
            break
        cursor = body["cursor"]
    assert seen == ["clip-b", "clip-a"]


def test_purge_clipping_keeps_the_row(client, env):
    """The resurrection guard: the tombstone stays so the device cannot bring it back."""
    _upload(client, "clip-a")
    clip_id = _clipping_id(env["db"], "clip-a")
    client.delete(f"/api/clippings/{clip_id}")
    before = _one(env["db"], "SELECT COUNT(*) FROM clippings")

    body = client.delete(f"/api/trash/clippings/{clip_id}?confirm=1").json()
    assert body["row_retained"] is True
    assert "resurrect" in body["reason"]
    assert _one(env["db"], "SELECT COUNT(*) FROM clippings") == before
    assert _one(env["db"], "SELECT trashed_text FROM clippings WHERE id = ?", (clip_id,)) == ""


def test_old_tombstone_is_not_restorable(client, env):
    _upload(client, "clip-a")
    clip_id = _clipping_id(env["db"], "clip-a")
    _exec(env["db"],
          "UPDATE clippings SET deleted = 1, text = '', trashed_text = '' WHERE id = ?",
          (clip_id,))

    res = client.post(f"/api/clippings/{clip_id}/restore")
    assert res.status_code == 409
    assert "older tombstone" in res.json()["detail"]


def test_restore_of_an_unknown_clipping_is_404(client, env):
    assert client.post("/api/clippings/9999/restore").status_code == 404


def test_device_tombstone_appears_in_trash_with_source_device(client, env):
    _upload(client, "clip-a")
    client.put(SYNC_URL, json={"items": [{"id": "clip-a", "deleted": 1}]})

    bucket = client.get("/api/trash").json()["clippings"]
    assert len(bucket) == 1
    assert bucket[0]["deleted_source"] == "device"
    assert bucket[0]["text"] == PASSAGE
    assert bucket[0]["restorable"] is True


def test_device_reupload_clears_the_trash_state(client, env):
    _upload(client, "clip-a")
    clip_id = _clipping_id(env["db"], "clip-a")
    client.delete(f"/api/clippings/{clip_id}")
    _upload(client, "clip-a")

    deleted, trashed, source, at = _rows(
        env["db"],
        "SELECT deleted, trashed_text, deleted_source, deleted_at FROM clippings WHERE id = ?",
        (clip_id,),
    )[0]
    assert (deleted, trashed, source, at) == (0, "", "", None)
    assert client.get("/api/trash").json()["counts"]["clippings"] == 0


# ------------------------------------------------------------------ #
# The trash view                                                       #
# ------------------------------------------------------------------ #

def test_trash_returns_named_buckets_with_counts(client, env):
    shot = _seed_screenshot(env["db"])
    highlight_id = _seed_highlight(client, shot)
    client.delete(f"/api/highlights/{highlight_id}")
    _upload(client, "clip-a")
    client.delete(f"/api/clippings/{_clipping_id(env['db'], 'clip-a')}")

    body = client.get("/api/trash").json()
    assert body["counts"] == {"highlights": 1, "clippings": 1, "writing": 0}
    assert body["highlights"][0]["selected_text"] == PASSAGE
    assert body["highlights"][0]["book_title"] == "Pastoral"
    assert body["clippings"][0]["chapter"] == "Chapter One"


def test_trash_returns_an_empty_writing_bucket_without_the_table(client, env):
    body = client.get("/api/trash").json()
    assert body["writing"] == []
    assert body["counts"]["writing"] == 0


def test_trash_lists_soft_deleted_writing_entries(client, writing):
    entry, _ = _seed_citation(writing["db"], "clipping", 1)
    _exec(writing["db"],
          "UPDATE writing_entries SET deleted_at = CURRENT_TIMESTAMP, "
          "body = ? WHERE id = ?",
          ("What stayed with me was this: ((ref:1))", entry))

    bucket = client.get("/api/trash").json()
    assert bucket["counts"]["writing"] == 1
    row = bucket["writing"][0]
    assert row["title"] == "On attention"
    assert row["excerpt"] == f"What stayed with me was this: {PASSAGE}"
    assert row["ref_count"] == 1
    assert row["restorable"] is True


def test_empty_trash_reports_writing_zero_when_the_table_is_missing(client, env):
    shot = _seed_screenshot(env["db"])
    client.delete(f"/api/highlights/{_seed_highlight(client, shot)}")
    _upload(client, "clip-a")
    client.delete(f"/api/clippings/{_clipping_id(env['db'], 'clip-a')}")

    body = client.post("/api/trash/empty?confirm=1").json()
    assert body["purged"] == {"highlights": 1, "clippings": 1, "writing": 0}
    assert body["rows_retained"] == 1
    assert _one(env["db"], "SELECT COUNT(*) FROM clippings") == 1
    assert _one(env["db"], "SELECT COUNT(*) FROM highlights") == 0


# ------------------------------------------------------------------ #
# Citations                                                            #
# ------------------------------------------------------------------ #

def test_deleting_a_cited_clipping_flips_the_ref_and_leaves_the_body_alone(client, writing):
    _upload(client, "clip-a")
    clip_id = _clipping_id(writing["db"], "clip-a")
    entry, ref = _seed_citation(writing["db"], "clipping", clip_id)
    body_before = _one(writing["db"], "SELECT body FROM writing_entries WHERE id = ?", (entry,))

    res = client.delete(f"/api/clippings/{clip_id}").json()
    assert res["writing_refs_marked"] == 1
    assert _one(writing["db"], "SELECT source_state FROM writing_refs WHERE id = ?",
                (ref,)) == "deleted"
    assert _one(writing["db"], "SELECT body FROM writing_entries WHERE id = ?",
                (entry,)) == body_before
    assert _one(writing["db"], "SELECT snapshot FROM writing_refs WHERE id = ?",
                (ref,)) == PASSAGE


def test_undo_flips_the_ref_back_to_live(client, writing):
    _upload(client, "clip-a")
    clip_id = _clipping_id(writing["db"], "clip-a")
    _, ref = _seed_citation(writing["db"], "clipping", clip_id)
    token = client.delete(f"/api/clippings/{clip_id}").json()["undo_token"]

    result = client.post("/api/undo", json={"token": token}).json()["result"]
    assert result["writing_refs_restored"] == 1
    assert _one(writing["db"], "SELECT source_state FROM writing_refs WHERE id = ?",
                (ref,)) == "live"


def test_restore_from_trash_flips_the_ref_back(client, writing):
    _upload(client, "clip-a")
    clip_id = _clipping_id(writing["db"], "clip-a")
    _, ref = _seed_citation(writing["db"], "clipping", clip_id)
    client.delete(f"/api/clippings/{clip_id}")

    res = client.post(f"/api/clippings/{clip_id}/restore").json()
    assert res["writing_refs_restored"] == 1
    assert _one(writing["db"], "SELECT source_state FROM writing_refs WHERE id = ?",
                (ref,)) == "live"


def test_delete_does_not_overwrite_a_drifted_ref(client, writing):
    _upload(client, "clip-a")
    clip_id = _clipping_id(writing["db"], "clip-a")
    _, ref = _seed_citation(writing["db"], "clipping", clip_id, state="drifted")

    token = client.delete(f"/api/clippings/{clip_id}").json()["undo_token"]
    assert _one(writing["db"], "SELECT source_state FROM writing_refs WHERE id = ?",
                (ref,)) == "drifted"
    client.post("/api/undo", json={"token": token})
    assert _one(writing["db"], "SELECT source_state FROM writing_refs WHERE id = ?",
                (ref,)) == "drifted"


def test_deleting_a_cited_highlight_flips_its_refs(client, writing):
    shot = _seed_screenshot(writing["db"])
    highlight_id = _seed_highlight(client, shot)
    _, ref = _seed_citation(writing["db"], "highlight", highlight_id)

    res = client.delete(f"/api/highlights/{highlight_id}").json()
    assert res["writing_refs_marked"] == 1
    assert _one(writing["db"], "SELECT source_state FROM writing_refs WHERE id = ?",
                (ref,)) == "deleted"


def test_purging_a_clipping_keeps_the_snapshot(client, writing):
    _upload(client, "clip-a")
    clip_id = _clipping_id(writing["db"], "clip-a")
    _, ref = _seed_citation(writing["db"], "clipping", clip_id)
    client.delete(f"/api/clippings/{clip_id}")
    client.delete(f"/api/trash/clippings/{clip_id}?confirm=1")

    snapshot, state = _rows(
        writing["db"], "SELECT snapshot, source_state FROM writing_refs WHERE id = ?", (ref,)
    )[0]
    assert snapshot == PASSAGE
    assert state == "deleted"


def test_citation_counters_are_zero_without_the_writing_tables(client, env):
    shot = _seed_screenshot(env["db"])
    highlight_id = _seed_highlight(client, shot)
    res = client.delete(f"/api/highlights/{highlight_id}")
    assert res.status_code == 200
    assert res.json()["writing_refs_marked"] == 0


def test_toast_label_names_the_citations(client, writing):
    _upload(client, "clip-a")
    clip_id = _clipping_id(writing["db"], "clip-a")
    _seed_citation(writing["db"], "clipping", clip_id)
    client.delete(f"/api/clippings/{clip_id}")
    one = client.get("/api/undo/recent").json()["items"][0]["label"]
    assert one.endswith(" 1 citation keeps its text.")

    _upload(client, "clip-b")
    other = _clipping_id(writing["db"], "clip-b")
    _seed_citation(writing["db"], "clipping", other)
    _seed_citation(writing["db"], "clipping", other)
    client.delete(f"/api/clippings/{other}")
    two = client.get("/api/undo/recent").json()["items"][0]["label"]
    assert two.endswith(" 2 citations keep its text.")
