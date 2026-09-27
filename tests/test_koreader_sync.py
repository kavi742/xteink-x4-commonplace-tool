import hashlib
import os
import tempfile

import pytest
from fastapi.testclient import TestClient

# Use a real temp file so ProgressStore uses per-operation connections
# (avoids :memory: isolation issues where each new connection is empty)
_tmpdb = tempfile.mktemp(suffix=".db")
os.environ["KOREADER_DB"] = _tmpdb

from xteink_service.koreader_sync import app, _store
import xteink_service.koreader_sync as ks

client = TestClient(app)


def test_auth_create():
    r = client.post("/users/create")
    assert r.status_code == 200
    assert r.json()["authorized"] == "OK"


def test_auth_check():
    r = client.get("/users/auth")
    assert r.status_code == 200
    assert r.json()["authorized"] == "OK"


def test_post_progress_stores_and_returns():
    r = client.post("/syncs/progress", json={
        "document": "Pastoral.epub",
        "progress": "0/Chapter8",
        "percentage": 22.5,
        "device": "xteink-x4",
        "device_id": "abc123",
    })
    assert r.status_code == 200
    body = r.json()
    assert body["document"] == "Pastoral.epub"
    assert body["percentage"] == 22.5


def test_put_progress_also_works():
    r = client.put("/syncs/progress", json={
        "document": "Another.epub",
        "progress": "0",
        "percentage": 5.0,
    })
    assert r.status_code == 200


def test_get_progress_returns_latest():
    client.post("/syncs/progress", json={
        "document": "Book.epub", "progress": "0/Ch1", "percentage": 10.0
    })
    client.post("/syncs/progress", json={
        "document": "Book.epub", "progress": "0/Ch5", "percentage": 50.0
    })
    r = client.get("/syncs/progress/Book.epub")
    assert r.status_code == 200
    assert r.json()["percentage"] == 50.0


def test_get_progress_unknown_document_returns_empty():
    r = client.get("/syncs/progress/nonexistent.epub")
    assert r.status_code == 200
    assert r.json() == {}


def test_list_progress_returns_all():
    r = client.get("/syncs/progress")
    assert r.status_code == 200
    assert isinstance(r.json(), list)


def test_kosync_auth_off_by_default():
    # No KOSYNC_USER/PASSWORD configured at import -> endpoints stay open.
    assert ks._KOSYNC_REQUIRED is False
    assert client.get("/users/auth").status_code == 200


def test_kosync_auth_rejects_missing_and_wrong_credentials(monkeypatch):
    key = hashlib.md5(b"s3cret").hexdigest()
    monkeypatch.setattr(ks, "_KOSYNC_USER", "reader")
    monkeypatch.setattr(ks, "_KOSYNC_KEY", key)
    monkeypatch.setattr(ks, "_KOSYNC_REQUIRED", True)

    assert client.get("/users/auth").status_code == 401  # no headers
    assert client.get("/users/auth", headers={
        "x-auth-user": "nope", "x-auth-key": key}).status_code == 401  # wrong user
    assert client.get("/users/auth", headers={
        "x-auth-user": "reader", "x-auth-key": "deadbeef"}).status_code == 401  # wrong key
    assert client.post("/syncs/progress", json={  # protects progress too
        "document": "x.epub", "progress": "0", "percentage": 1.0}).status_code == 401


def test_kosync_auth_accepts_valid_credentials(monkeypatch):
    key = hashlib.md5(b"s3cret").hexdigest()
    monkeypatch.setattr(ks, "_KOSYNC_USER", "reader")
    monkeypatch.setattr(ks, "_KOSYNC_KEY", key)
    monkeypatch.setattr(ks, "_KOSYNC_REQUIRED", True)
    hdr = {"x-auth-user": "reader", "x-auth-key": key.upper()}  # key compared case-insensitively

    assert client.get("/users/auth", headers=hdr).status_code == 200
    r = client.post("/syncs/progress", headers=hdr, json={
        "document": "auth.epub", "progress": "0/Ch1", "percentage": 12.0})
    assert r.status_code == 200
    assert r.json()["document"] == "auth.epub"


def test_public_sync_host_blocks_ui_and_api(monkeypatch):
    monkeypatch.setattr(ks, "_PUBLIC_SYNC_HOST", "sync.example.org")
    pub = {"host": "sync.example.org"}
    # On the public sync host, only the kosync endpoints are reachable.
    assert client.get("/status", headers=pub).status_code == 404
    assert client.get("/api/books", headers=pub).status_code == 404
    assert client.get("/users/auth", headers=pub).status_code == 200
    # From any other Host (LAN / Tailscale), the UI + API are unaffected.
    assert client.get("/status", headers={"host": "ghostbird:8090"}).status_code == 200


# ------------------------------------------------------------------ #
# Multi-device sync (X4 + a Kindle running KOReader)                   #
# ------------------------------------------------------------------ #

def test_devices_are_registered_per_client():
    client.post("/syncs/progress", json={
        "document": "multi.epub", "progress": "0/Ch1", "percentage": 0.1,
        "device": "CrossPoint", "device_id": "crosspoint-reader"})
    client.post("/syncs/progress", json={
        "document": "multi.epub", "progress": "0/Ch4", "percentage": 0.4,
        "device": "KindleVoyage", "device_id": "kindle-abc"})

    by_id = {d["device_id"]: d for d in _store.devices()}
    assert by_id["crosspoint-reader"]["device"] == "CrossPoint"
    assert by_id["kindle-abc"]["device"] == "KindleVoyage"
    assert by_id["kindle-abc"]["book_count"] >= 1


def test_device_recorded_even_when_position_is_deduped():
    """A device that only re-sends a position it pulled from here is still known."""
    client.post("/syncs/progress", json={
        "document": "dedup.epub", "progress": "0/Ch2", "percentage": 0.2,
        "device": "CrossPoint", "device_id": "crosspoint-reader"})
    client.post("/syncs/progress", json={
        "document": "dedup.epub", "progress": "0/Ch2", "percentage": 0.2,
        "device": "KindleVoyage", "device_id": "kindle-quiet"})

    assert "kindle-quiet" in {d["device_id"] for d in _store.devices()}
    # ...without creating a duplicate progress row.
    assert len(_store._latest_n("dedup.epub", 10)) == 1


def test_deduped_push_is_logged(caplog):
    """This path stores nothing, so the log is the only way to tell a received
    push from one that never arrived."""
    client.post("/syncs/progress", json={
        "document": "quiet.epub", "progress": "0/Ch2", "percentage": 0.2,
        "device": "KindleVoyage", "device_id": "kindle-abc"})

    with caplog.at_level("INFO", logger="xteink_service.koreader_sync"):
        client.post("/syncs/progress", json={
            "document": "quiet.epub", "progress": "0/Ch2", "percentage": 0.2,
            "device": "KindleVoyage", "device_id": "kindle-abc",
            "metadata": {"title": "Quiet Book"}})

    assert "Position unchanged" in caplog.text
    assert "KindleVoyage" in caplog.text
    assert "metadata: yes" in caplog.text


def test_devices_for_document_tracks_each_reader():
    client.post("/syncs/progress", json={
        "document": "shared.epub", "progress": "0/Ch1", "percentage": 0.1,
        "device": "CrossPoint", "device_id": "crosspoint-reader"})
    assert _store.devices_for_document("shared.epub") == ["CrossPoint"]

    client.post("/syncs/progress", json={
        "document": "shared.epub", "progress": "0/Ch9", "percentage": 0.9,
        "device": "KindleVoyage", "device_id": "kindle-abc"})
    assert set(_store.devices_for_document("shared.epub")) == {"CrossPoint", "KindleVoyage"}


def test_api_devices_marks_only_the_x4_as_scannable():
    client.post("/syncs/progress", json={
        "document": "scan.epub", "progress": "0/Ch1", "percentage": 0.1,
        "device": "CrossPoint", "device_id": "crosspoint-reader"})
    client.post("/syncs/progress", json={
        "document": "scan.epub", "progress": "0/Ch3", "percentage": 0.3,
        "device": "KindleVoyage", "device_id": "kindle-abc"})

    devices = {d["device_id"]: d for d in client.get("/api/devices").json()}
    assert devices["crosspoint-reader"]["scannable"] is True
    assert devices["kindle-abc"]["scannable"] is False


# ------------------------------------------------------------------ #
# KOReader's optional `metadata` payload                               #
# ------------------------------------------------------------------ #

def test_metadata_payload_resolves_title_and_author(tmp_path, monkeypatch):
    """KOReader's "Send document metadata" is the only automatic title source
    for a device whose file listing we can't scan."""
    from xteink_service.state import SyncState
    state_db = str(tmp_path / "state.db")
    monkeypatch.setenv("STATE_DB", state_db)
    SyncState(state_db)  # create schema

    r = client.post("/syncs/progress", json={
        "document": "kindlehash1", "progress": "/body/p[1]", "percentage": 0.01,
        "device": "KindleVoyage", "device_id": "kindle-abc",
        "metadata": {
            "filename": "Son of Nobody.epub",
            "title": "Son of Nobody",
            "authors": "Yann Martel",
        },
    })
    assert r.status_code == 200
    assert SyncState(state_db).get_title("kindlehash1") == "Son of Nobody"

    stored = _store._latest("kindlehash1")
    assert stored["title"] == "Son of Nobody"
    assert stored["author"] == "Yann Martel"


def test_metadata_falls_back_to_filename_and_joins_authors(tmp_path, monkeypatch):
    from xteink_service.state import SyncState
    state_db = str(tmp_path / "state.db")
    monkeypatch.setenv("STATE_DB", state_db)
    SyncState(state_db)

    client.post("/syncs/progress", json={
        "document": "kindlehash2", "progress": "0", "percentage": 0.02,
        "device": "KindleVoyage", "device_id": "kindle-abc",
        "metadata": {"filename": "Beatrice and Virgil.epub",
                     "authors": "Yann Martel\nSomeone Else"},
    })
    assert SyncState(state_db).get_title("kindlehash2") == "Beatrice and Virgil"
    assert _store._latest("kindlehash2")["author"] == "Yann Martel, Someone Else"


def test_manual_alias_wins_over_client_metadata(tmp_path, monkeypatch):
    from xteink_service.state import SyncState
    state_db = str(tmp_path / "state.db")
    monkeypatch.setenv("STATE_DB", state_db)
    state = SyncState(state_db)
    state.set_title("kindlehash3", "The Corrected Title")

    client.post("/syncs/progress", json={
        "document": "kindlehash3", "progress": "0", "percentage": 0.03,
        "device": "KindleVoyage", "device_id": "kindle-abc",
        "metadata": {"title": "asin_B0012345"},
    })
    assert state.get_title("kindlehash3") == "The Corrected Title"


def test_crosspoint_metadata_payload_resolves_title(tmp_path, monkeypatch):
    """Exact body CrossPoint sends with Send Metadata on — see
    KOReaderSyncClient::updateProgress in the crosspoint-reader firmware."""
    from xteink_service.state import SyncState
    state_db = str(tmp_path / "state.db")
    monkeypatch.setenv("STATE_DB", state_db)
    SyncState(state_db)

    r = client.post("/syncs/progress", json={
        "document": "b2ba363dca7d987ce457af86c6d91002",
        "metadata": {
            "filename": "Piranesi - Susanna Clarke.epub",
            "title": "Piranesi",
            "authors": "Susanna Clarke",
        },
        "progress": "/body/DocFragment[5]/body/section[1]/p[9]/text()[1].1559",
        "percentage": 0.049219,
        "device": "CrossPoint",
        "device_id": "crosspoint-reader",
    })
    assert r.status_code == 200
    state = SyncState(state_db)
    assert state.get_title("b2ba363dca7d987ce457af86c6d91002") == "Piranesi"
    # The filename is kept too: the X4 digests md5(filename), so it's verifiable.
    assert _store._latest("b2ba363dca7d987ce457af86c6d91002")["author"] == "Susanna Clarke"


def test_crosspoint_rich_position_is_captured_if_ever_sent():
    """The firmware gates its rich `position` object to sync.crosspointreader.com,
    so we don't expect it — but capture it rather than drop it if that changes."""
    ks._seen_extra_shapes.clear()
    client.post("/syncs/progress", json={
        "document": "richpos", "progress": "/body/DocFragment[2]", "percentage": 0.2,
        "device": "CrossPoint", "device_id": "crosspoint-reader",
        "position": {"pctQ": 200000, "spine": 2, "page": 4, "pages": 30, "para": 7},
    })
    import json
    assert json.loads(_store._latest("richpos")["extra"])["position"]["pages"] == 30


def test_progress_without_metadata_still_works():
    """The official kosync payload has no metadata field — must not 422."""
    r = client.post("/syncs/progress", json={
        "document": "nometa", "progress": "0", "percentage": 0.5,
        "device": "CrossPoint", "device_id": "crosspoint-reader"})
    assert r.status_code == 200
    assert "metadata" not in r.json()


# ------------------------------------------------------------------ #
# Unmodelled payload fields                                            #
# ------------------------------------------------------------------ #

def test_unmodelled_fields_are_kept_not_dropped():
    """Clients add payload fields between releases; keep them so they're visible."""
    ks._seen_extra_shapes.clear()
    r = client.post("/syncs/progress", json={
        "document": "extras", "progress": "0/Ch1", "percentage": 0.25,
        "device": "CrossPoint", "device_id": "crosspoint-reader",
        "page": 42, "total_pages": 300, "chapter": "Chapter One"})
    assert r.status_code == 200
    # ...and are not echoed back in the kosync response the client parses.
    assert set(r.json()) == {
        "document", "progress", "percentage", "device", "device_id", "timestamp"}

    import json
    stored = json.loads(_store._latest("extras")["extra"])
    assert stored == {"page": 42, "total_pages": 300, "chapter": "Chapter One"}


# ------------------------------------------------------------------ #
# Cross-device sync (X4 <-> Kindle)                                    #
# ------------------------------------------------------------------ #

def test_linked_hashes_share_the_newest_position(tmp_path, monkeypatch):
    """Two devices holding different copies of one book produce different
    digests; sharing a title is what links them."""
    from xteink_service.state import SyncState
    state_db = str(tmp_path / "state.db")
    monkeypatch.setenv("STATE_DB", state_db)
    state = SyncState(state_db)
    state.set_title("x4hash", "Life of Pi - Yann Martel")
    state.set_title("kindlehash", "Life of Pi")  # same book, canonical slug matches

    client.post("/syncs/progress", json={
        "document": "x4hash", "progress": "/body/DocFragment[2]", "percentage": 0.2,
        "device": "CrossPoint", "device_id": "crosspoint-reader"})
    client.post("/syncs/progress", json={
        "document": "kindlehash", "progress": "/body/DocFragment[7]", "percentage": 0.7,
        "device": "KindleVoyage", "device_id": "kindle-abc"})

    # The X4 asks about its own copy and gets the Kindle's newer position,
    # attributed to the Kindle so KOReader knows it isn't its own.
    body = client.get("/syncs/progress/x4hash").json()
    assert body["percentage"] == 0.7
    assert body["device"] == "KindleVoyage"
    assert body["document"] == "x4hash"


def test_unlinked_hashes_stay_independent(tmp_path, monkeypatch):
    from xteink_service.state import SyncState
    state_db = str(tmp_path / "state.db")
    monkeypatch.setenv("STATE_DB", state_db)
    state = SyncState(state_db)
    state.set_title("bookA", "Piranesi - Susanna Clarke")
    state.set_title("bookB", "Life of Pi - Yann Martel")

    client.post("/syncs/progress", json={
        "document": "bookA", "progress": "0/Ch1", "percentage": 0.1,
        "device": "CrossPoint", "device_id": "crosspoint-reader"})
    client.post("/syncs/progress", json={
        "document": "bookB", "progress": "0/Ch9", "percentage": 0.9,
        "device": "KindleVoyage", "device_id": "kindle-abc"})

    assert client.get("/syncs/progress/bookA").json()["percentage"] == 0.1


def test_unaliased_hash_falls_back_to_its_own_history(tmp_path, monkeypatch):
    monkeypatch.setenv("STATE_DB", str(tmp_path / "empty.db"))
    client.post("/syncs/progress", json={
        "document": "loner", "progress": "0/Ch3", "percentage": 0.3,
        "device": "CrossPoint", "device_id": "crosspoint-reader"})
    assert client.get("/syncs/progress/loner").json()["percentage"] == 0.3
