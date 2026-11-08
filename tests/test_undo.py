"""Undo log tests.

The undo token is the cheap recovery path: ten seconds of toast on top of a
trash that holds the item indefinitely. Everything asserted here is a contract
with either the device (revisions and pagination) or the user (a double-click
must not restore twice).
"""
import json
import sqlite3
import time

import pytest
from fastapi.testclient import TestClient

from xteink_service import clippings, undo
from xteink_service.koreader_sync import app
from xteink_service.state import SyncState

DOC = "bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb1"
SYNC_URL = f"/api/v1/clippings/{DOC}"
PASSAGE = "A sentence worth keeping."


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


def _rows(db: str, sql: str, params: tuple = ()) -> list[tuple]:
    with sqlite3.connect(db) as conn:
        return conn.execute(sql, params).fetchall()


def _item(clip_id: str, text: str = PASSAGE, **over) -> dict:
    item = {
        "id": clip_id, "spine": 1, "start_page": 2, "end_page": 2, "pages": 1,
        "start_word": 0, "end_word": 5, "words": 5, "chapter": "Chapter One",
        "text": text, "created_at": 1793000000, "layout_signature": 7,
    }
    item.update(over)
    return item


def _upload(client, *clip_ids: str) -> None:
    for clip_id in clip_ids:
        assert client.put(SYNC_URL, json={"items": [_item(clip_id)]}).status_code == 200


def _clipping_id(db: str, clip_id: str) -> int:
    return _rows(db, "SELECT id FROM clippings WHERE clip_id = ?", (clip_id,))[0][0]


def _revision(db: str, clipping_id: int) -> int:
    return _rows(db, "SELECT revision FROM clippings WHERE id = ?", (clipping_id,))[0][0]


def _note(env) -> "object":
    return env["vault"] / "Books" / "Pastoral.md"


def _seed_screenshot(db: str, book: str = "Pastoral", ocr: str = PASSAGE) -> int:
    with sqlite3.connect(db) as conn:
        return conn.execute(
            "INSERT INTO synced_screenshots "
            "(device_path, content_hash, book_title, sync_date, ocr_text, vault_png_path) "
            "VALUES (?,?,?,?,?,?)",
            (f"/screenshots/{book}.bmp", f"hash-{book}", book, "2026-07-04", ocr, "x.png"),
        ).lastrowid


def _seed_highlight(client, screenshot_id: int, text: str = PASSAGE) -> int:
    res = client.post(
        f"/api/screenshots/{screenshot_id}/highlights", json={"selected_text": text}
    )
    assert res.status_code == 200
    return res.json()["id"]


def _walk(client, start: int = 0, limit: int = 1) -> tuple[int, list[dict]]:
    """Paginate like the firmware does, asserting its strict cursor rules."""
    cursor, pages = start, []
    for _ in range(50):
        body = client.get(
            SYNC_URL, params={"cursor": cursor, "limit": limit, "format": "reader"}
        ).json()
        assert len(body["items"]) <= limit
        assert body["cursor"] >= cursor, "cursor moved backwards"
        if body["items"]:
            assert body["cursor"] > cursor, "cursor must advance when items are returned"
        else:
            assert body["cursor"] == cursor
            assert body["more"] is False, "more must never be true on an empty page"
        pages.append(body)
        cursor = body["cursor"]
        if not body["items"]:
            return cursor, pages
    raise AssertionError("pagination did not terminate")


# ------------------------------------------------------------------ #
# Module shape                                                         #
# ------------------------------------------------------------------ #

def test_undo_log_init_is_idempotent(env):
    undo.init_db()
    undo.init_db()


def test_undo_module_reads_state_db_inside_a_function(tmp_path, monkeypatch):
    first = str(tmp_path / "first.db")
    monkeypatch.setenv("STATE_DB", first)
    assert undo._state_db() == first
    second = str(tmp_path / "second.db")
    monkeypatch.setenv("STATE_DB", second)
    assert undo._state_db() == second


def test_every_destructive_route_returns_an_undo_token(client, env):
    shot = _seed_screenshot(env["db"])
    highlight_id = _seed_highlight(client, shot)
    _upload(client, "clip-a")
    clip_id = _clipping_id(env["db"], "clip-a")

    assert client.delete(f"/api/highlights/{highlight_id}").json()["undo_token"]
    assert client.delete(f"/api/clippings/{clip_id}").json()["undo_token"]

    purges = (
        client.delete(f"/api/trash/highlights/{highlight_id}?confirm=1"),
        client.delete(f"/api/trash/clippings/{clip_id}?confirm=1"),
        client.post("/api/trash/empty?confirm=1"),
    )
    for res in purges:
        assert res.status_code == 200
        assert res.json()["undo_token"] is None


# ------------------------------------------------------------------ #
# Idempotency and failure modes                                        #
# ------------------------------------------------------------------ #

def test_undo_is_idempotent(client, env):
    """The double-click guard: a second call reverses nothing."""
    _upload(client, "clip-a")
    clip_id = _clipping_id(env["db"], "clip-a")
    token = client.delete(f"/api/clippings/{clip_id}").json()["undo_token"]

    first = client.post("/api/undo", json={"token": token})
    assert first.status_code == 200
    assert first.json()["already_undone"] is False
    after_first = _revision(env["db"], clip_id)

    second = client.post("/api/undo", json={"token": token})
    assert second.status_code == 200
    assert second.json()["already_undone"] is True
    assert second.json()["result"] == {}
    assert _revision(env["db"], clip_id) == after_first


def test_unknown_undo_token_returns_404(client, env):
    assert client.post("/api/undo", json={"token": "deadbeef"}).status_code == 404


def test_expired_undo_token_returns_410(client, env):
    _upload(client, "clip-a")
    clip_id = _clipping_id(env["db"], "clip-a")
    client.delete(f"/api/clippings/{clip_id}")
    token = undo.record("clipping_delete", "Deleted 1 clipping",
                        {"clippings": [{"id": clip_id}], "writing_refs": []}, ttl_s=-1)

    res = client.post("/api/undo", json={"token": token})
    assert res.status_code == 410

    # An expired toast must never mean lost data.
    assert client.post(f"/api/clippings/{clip_id}/restore").status_code == 200


def test_undo_of_an_unregistered_kind_returns_409(client, env):
    token = undo.record("not_a_real_kind", "Nothing", {})
    res = client.post("/api/undo", json={"token": token})
    assert res.status_code == 409
    assert "not_a_real_kind" in res.json()["detail"]


def test_undo_skips_a_purged_target(client, env):
    _upload(client, "clip-a")
    clip_id = _clipping_id(env["db"], "clip-a")
    token = client.delete(f"/api/clippings/{clip_id}").json()["undo_token"]
    client.delete(f"/api/trash/clippings/{clip_id}?confirm=1")

    body = client.post("/api/undo", json={"token": token}).json()
    assert body["result"]["skipped"] == [clip_id]
    assert body["result"]["restored"] == []
    assert _rows(env["db"], "SELECT deleted FROM clippings WHERE id = ?", (clip_id,))[0][0] == 1


def test_sweep_keeps_recently_undone_rows(client, env):
    _upload(client, "clip-a")
    clip_id = _clipping_id(env["db"], "clip-a")
    token = client.delete(f"/api/clippings/{clip_id}").json()["undo_token"]
    client.post("/api/undo", json={"token": token})

    undo.init_db()
    assert client.post("/api/undo", json={"token": token}).json()["already_undone"] is True


# ------------------------------------------------------------------ #
# Revisions and the device contract                                    #
# ------------------------------------------------------------------ #

def test_undo_clipping_delete_allocates_a_new_revision(client, env):
    _upload(client, "clip-a")
    clip_id = _clipping_id(env["db"], "clip-a")
    before_delete = _revision(env["db"], clip_id)

    token = client.delete(f"/api/clippings/{clip_id}").json()["undo_token"]
    delete_revision = _revision(env["db"], clip_id)
    payload = json.loads(
        _rows(env["db"], "SELECT payload FROM undo_log WHERE token = ?", (token,))[0][0]
    )
    assert payload["clippings"][0]["delete_revision"] == delete_revision

    client.post("/api/undo", json={"token": token})
    restored = _revision(env["db"], clip_id)
    assert restored > delete_revision
    assert restored != before_delete


def test_restore_takes_a_strictly_greater_revision_than_the_delete(client, env):
    _upload(client, "clip-a")
    clip_id = _clipping_id(env["db"], "clip-a")
    delete_revision = client.delete(f"/api/clippings/{clip_id}").json()["revision"]
    restored = client.post(f"/api/clippings/{clip_id}/restore").json()
    assert restored["revision"] > delete_revision


def test_pagination_contract_survives_an_undo(client, env):
    """Upload, delete, undo: the cursor never moves backwards."""
    _upload(client, "clip-a", "clip-b")
    cursor, pages = _walk(client)
    assert [p["items"][0]["id"] for p in pages if p["items"]] == ["clip-a", "clip-b"]

    clip_id = _clipping_id(env["db"], "clip-a")
    token = client.delete(f"/api/clippings/{clip_id}").json()["undo_token"]
    cursor, pages = _walk(client, start=cursor)
    tombstone = [p["items"][0] for p in pages if p["items"]]
    assert tombstone and tombstone[0] == {"id": "clip-a", "deleted": 1,
                                          "revision": tombstone[0]["revision"]}

    client.post("/api/undo", json={"token": token})
    cursor, pages = _walk(client, start=cursor)
    revived = [p["items"][0] for p in pages if p["items"]]
    assert len(revived) == 1
    assert revived[0]["id"] == "clip-a"
    assert revived[0]["deleted"] == 0
    assert revived[0]["text"] == PASSAGE


def test_undo_does_not_leak_trashed_text_onto_the_wire(client, env):
    _upload(client, "clip-a")
    clip_id = _clipping_id(env["db"], "clip-a")
    client.delete(f"/api/clippings/{clip_id}")
    body = client.get(SYNC_URL, params={"cursor": 0, "limit": 10}).json()
    assert body["items"][0].keys() == {"id", "deleted", "revision"}


# ------------------------------------------------------------------ #
# Labels and rehydration                                               #
# ------------------------------------------------------------------ #

def test_toast_label_differs_by_deleted_source(client, env):
    _upload(client, "clip-a", "clip-b")
    clip_id = _clipping_id(env["db"], "clip-a")
    client.delete(f"/api/clippings/{clip_id}")
    client.put(SYNC_URL, json={"items": [{"id": "clip-b", "deleted": 1}]})

    labels = [item["label"] for item in client.get("/api/undo/recent").json()["items"]]
    assert "Deleted 1 clipping" in labels
    assert (
        "The reader deleted 1 clipping. Undo sends it back at the next sync." in labels
    )


def test_device_tombstone_batch_records_one_undo_entry(client, env):
    _upload(client, "clip-a", "clip-b", "clip-c")
    client.put(SYNC_URL, json={"items": [
        {"id": "clip-a", "deleted": 1},
        {"id": "clip-b", "deleted": 1},
        {"id": "clip-c", "deleted": 1},
    ]})

    items = client.get("/api/undo/recent").json()["items"]
    assert len(items) == 1
    assert items[0]["label"].startswith("The reader deleted 3 clippings.")


def test_a_repeated_device_tombstone_raises_no_second_toast(client, env):
    _upload(client, "clip-a")
    body = {"items": [{"id": "clip-a", "deleted": 1}]}
    client.put(SYNC_URL, json=body)
    client.put(SYNC_URL, json=body)
    assert len(client.get("/api/undo/recent").json()["items"]) == 1


def test_undo_after_reload_via_recent(client, env):
    """The client lost the token; the server still knows about it."""
    _upload(client, "clip-a")
    clip_id = _clipping_id(env["db"], "clip-a")
    client.delete(f"/api/clippings/{clip_id}")

    items = client.get("/api/undo/recent?limit=5").json()["items"]
    assert items and "payload" not in items[0]
    assert client.post("/api/undo", json={"token": items[0]["token"]}).status_code == 200
    assert _rows(env["db"], "SELECT deleted FROM clippings WHERE id = ?", (clip_id,))[0][0] == 0


def test_recent_excludes_undone_and_expired_entries(client, env):
    _upload(client, "clip-a")
    clip_id = _clipping_id(env["db"], "clip-a")
    undone = client.delete(f"/api/clippings/{clip_id}").json()["undo_token"]
    client.post("/api/undo", json={"token": undone})
    expired = undo.record("clipping_delete", "Deleted 1 clipping", {}, ttl_s=-1)

    tokens = [i["token"] for i in client.get("/api/undo/recent?limit=20").json()["items"]]
    assert undone not in tokens
    assert expired not in tokens


def test_recent_is_newest_first(client, env):
    _upload(client, "clip-a", "clip-b")
    first = client.delete(f"/api/clippings/{_clipping_id(env['db'], 'clip-a')}").json()
    second = client.delete(f"/api/clippings/{_clipping_id(env['db'], 'clip-b')}").json()
    tokens = [i["token"] for i in client.get("/api/undo/recent?limit=5").json()["items"]]
    assert tokens[0] == second["undo_token"]
    assert first["undo_token"] in tokens


# ------------------------------------------------------------------ #
# Highlights, the vault, and search                                    #
# ------------------------------------------------------------------ #

def test_undo_highlight_delete_restores_the_vault_marks(client, env):
    note = _note(env)
    note.write_text(f"## 2026-07-04\n\n> {PASSAGE}\n")
    shot = _seed_screenshot(env["db"])
    highlight_id = _seed_highlight(client, shot)
    assert f"=={PASSAGE}==" in note.read_text()

    deleted = client.delete(f"/api/highlights/{highlight_id}").json()
    assert deleted["unmarked_vault"] is True
    assert "==" not in note.read_text()

    body = client.post("/api/undo", json={"token": deleted["undo_token"]}).json()
    assert body["result"]["marked_vault"] is True
    assert f"=={PASSAGE}==" in note.read_text()


def test_undo_highlight_delete_restores_the_row(client, env):
    shot = _seed_screenshot(env["db"])
    highlight_id = _seed_highlight(client, shot)
    token = client.delete(f"/api/highlights/{highlight_id}").json()["undo_token"]
    client.post("/api/undo", json={"token": token})

    listed = client.get(f"/api/screenshots/{shot}/highlights").json()
    assert [h["id"] for h in listed] == [highlight_id]


def test_a_soft_deleted_item_leaves_search_and_comes_back_on_undo(client, env):
    shot = _seed_screenshot(env["db"], ocr="unrelated text")
    highlight_id = _seed_highlight(client, shot, "the pastoral mode of attention")

    def hits() -> list[int]:
        found = client.get("/api/search", params={"q": "pastoral", "kinds": "highlight"}).json()
        return [r["ref_id"] for r in found["results"]]

    assert hits() == [highlight_id]
    token = client.delete(f"/api/highlights/{highlight_id}").json()["undo_token"]
    assert hits() == []
    client.post("/api/undo", json={"token": token})
    assert hits() == [highlight_id]


def test_a_soft_deleted_clipping_leaves_search_and_comes_back_on_undo(client, env):
    _upload(client, "clip-a")
    clip_id = _clipping_id(env["db"], "clip-a")

    def hits() -> list[int]:
        found = client.get("/api/search", params={"q": "keeping", "kinds": "clipping"}).json()
        return [r["ref_id"] for r in found["results"]]

    assert hits() == [clip_id]
    token = client.delete(f"/api/clippings/{clip_id}").json()["undo_token"]
    assert hits() == []
    client.post("/api/undo", json={"token": token})
    assert hits() == [clip_id]


def test_a_reindex_does_not_resurrect_a_trashed_highlight(client, env):
    shot = _seed_screenshot(env["db"], ocr="unrelated text")
    highlight_id = _seed_highlight(client, shot, "the pastoral mode of attention")
    client.delete(f"/api/highlights/{highlight_id}")
    client.post("/api/search/reindex")

    found = client.get("/api/search", params={"q": "pastoral", "kinds": "highlight"}).json()
    assert found["results"] == []


def test_record_inside_an_open_transaction_does_not_deadlock(env):
    """clippings writes its undo row while holding BEGIN IMMEDIATE."""
    started = time.monotonic()
    conn = sqlite3.connect(env["db"])
    try:
        conn.execute("BEGIN IMMEDIATE")
        token = undo.record("clipping_delete", "Deleted 1 clipping", {}, conn=conn)
        conn.commit()
    finally:
        conn.close()
    assert time.monotonic() - started < 2
    assert _rows(env["db"], "SELECT kind FROM undo_log WHERE token = ?", (token,))
