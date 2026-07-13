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
