import os
import tempfile

import pytest
from fastapi.testclient import TestClient

# Use a real temp file so ProgressStore uses per-operation connections
# (avoids :memory: isolation issues where each new connection is empty)
_tmpdb = tempfile.mktemp(suffix=".db")
os.environ["KOREADER_DB"] = _tmpdb

from xteink_service.koreader_sync import app, _store

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
