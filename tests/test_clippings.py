"""
Clipping sync protocol tests.

The device-facing shapes here mirror CrossPoint's ClippingSync.cpp: it probes
for sync_version 2, uploads one item per PUT, and paginates with limit=1 while
rejecting any cursor that fails to advance.
"""
import sqlite3

import pytest
from fastapi.testclient import TestClient

from xteink_service.clippings import _state_db
from xteink_service.koreader_sync import app

client = TestClient(app)

DOC = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa1"
URL = f"/api/v1/clippings/{DOC}"


def _item(clip_id: str, text: str = "A sentence worth keeping.", **over) -> dict:
    item = {
        "id": clip_id,
        "spine": 3,
        "start_page": 10,
        "end_page": 10,
        "pages": 1,
        "start_word": 4,
        "end_word": 9,
        "words": 6,
        "para": 2,
        "chapter": "Chapter One",
        "text": text,
        "created_at": 1793000000,
        "layout_signature": 12345,
        "start_offset": 100,
        "end_offset": 200,
    }
    item.update(over)
    return item


@pytest.fixture(autouse=True)
def clean_table():
    yield
    with sqlite3.connect(_state_db()) as conn:
        try:
            conn.execute("DELETE FROM clippings")
        except sqlite3.OperationalError:
            pass


def test_probe_reports_sync_version_2():
    """The firmware aborts the whole sync unless the probe answers with 2."""
    r = client.get(URL, params={"cursor": 0, "limit": 1, "format": "reader"})
    assert r.status_code == 200
    body = r.json()
    assert body["sync_version"] == 2
    assert body["items"] == []
    assert body["more"] is False
    assert body["cursor"] == 0


def test_upload_then_download_roundtrip():
    assert client.put(URL, json={"items": [_item("clip-1")]}).json()["accepted"] == 1

    body = client.get(URL, params={"cursor": 0, "limit": 100}).json()
    assert len(body["items"]) == 1
    got = body["items"][0]
    assert got["id"] == "clip-1"
    assert got["text"] == "A sentence worth keeping."
    assert got["chapter"] == "Chapter One"
    assert got["deleted"] == 0
    assert got["revision"] == body["cursor"] > 0
    assert got["start_offset"] == 100 and got["end_offset"] == 200


def test_pagination_matches_device_expectations():
    """limit=1 walk: one item per page, cursor strictly advances, more clears."""
    for n in range(3):
        client.put(URL, json={"items": [_item(f"clip-{n}", text=f"Excerpt {n}")]})

    cursor, seen = 0, []
    for _ in range(3):
        body = client.get(URL, params={"cursor": cursor, "limit": 1, "format": "reader"}).json()
        assert len(body["items"]) <= 1
        assert body["cursor"] > cursor  # device errors on a non-advancing cursor
        assert not (body["more"] and not body["items"])
        seen.append(body["items"][0]["id"])
        cursor = body["cursor"]
    assert seen == ["clip-0", "clip-1", "clip-2"]

    final = client.get(URL, params={"cursor": cursor, "limit": 1}).json()
    assert final["items"] == []
    assert final["more"] is False
    assert final["cursor"] == cursor  # never moves backwards


def test_exhausted_cursor_is_held_not_reset():
    client.put(URL, json={"items": [_item("clip-1")]})
    first = client.get(URL, params={"cursor": 0, "limit": 1}).json()
    again = client.get(URL, params={"cursor": first["cursor"], "limit": 1}).json()
    assert again["cursor"] == first["cursor"]


def test_delete_is_a_tombstone_with_a_new_revision():
    client.put(URL, json={"items": [_item("clip-1")]})
    created = client.get(URL, params={"cursor": 0, "limit": 10}).json()["cursor"]

    assert client.put(URL, json={"items": [{"id": "clip-1", "deleted": 1}]}).json()["accepted"] == 1

    body = client.get(URL, params={"cursor": 0, "limit": 10}).json()
    assert len(body["items"]) == 1
    assert body["items"][0] == {"id": "clip-1", "deleted": 1, "revision": body["cursor"]}
    # A bumped revision is what makes other devices notice the removal.
    assert body["cursor"] > created


def test_reupload_bumps_revision_so_other_devices_resync():
    client.put(URL, json={"items": [_item("clip-1", text="first")]})
    first = client.get(URL, params={"cursor": 0, "limit": 10}).json()["cursor"]
    client.put(URL, json={"items": [_item("clip-1", text="second")]})
    body = client.get(URL, params={"cursor": 0, "limit": 10}).json()

    assert len(body["items"]) == 1  # still one row, not a duplicate
    assert body["items"][0]["text"] == "second"
    assert body["cursor"] > first


def test_clippings_are_scoped_to_their_document():
    client.put(URL, json={"items": [_item("clip-1")]})
    other = client.get("/api/v1/clippings/bbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb2",
                       params={"cursor": 0, "limit": 10}).json()
    assert other["items"] == []
    assert other["sync_version"] == 2


@pytest.mark.parametrize("bad", [
    {"id": ""},                           # empty
    {"id": "has space"},                  # non-printable-ASCII range
    {"id": "x" * 65},                     # longer than the device's char[65]
    {"id": "ok", "text": ""},             # empty text
    {"id": "ok", "text": "x" * 4097},     # over CLIPPING_TEXT_MAX
    {"id": "ok", "spine": 70000},         # out of uint16
    {"id": "ok", "start_offset": 500, "end_offset": 100},  # inverted offsets
])
def test_rejects_payloads_the_device_could_not_decode(bad):
    payload = _item("ok")
    payload.update(bad)
    r = client.put(URL, json={"items": [payload]})
    assert r.status_code == 422


def test_unknown_api_path_returns_json_404_not_the_spa():
    """The SPA shell used to answer 200 text/html, which broke the probe."""
    r = client.get("/api/definitely-not-a-route")
    assert r.status_code == 404
    assert "text/html" not in r.headers.get("content-type", "")


def test_ui_list_and_delete():
    client.put(URL, json={"items": [_item("clip-1")]})
    listed = client.get("/api/clippings").json()
    assert len(listed) == 1
    assert listed[0]["clip_id"] == "clip-1"

    assert client.delete(f"/api/clippings/{listed[0]['id']}").status_code == 200
    assert client.get("/api/clippings").json() == []
    # Deleting from the UI must still reach the device as a tombstone.
    synced = client.get(URL, params={"cursor": 0, "limit": 10}).json()
    assert synced["items"][0]["deleted"] == 1
