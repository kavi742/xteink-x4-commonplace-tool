"""
Tests for the in-memory log buffer (xteink_service.logbuffer) and the
/logs, /api/logs, /api/device endpoints. No hardware required.
"""
import logging

import pytest
from fastapi.testclient import TestClient

from xteink_service import logbuffer
from xteink_service.logbuffer import RingBufferHandler


# ------------------------------------------------------------------ #
# RingBufferHandler unit tests                                        #
# ------------------------------------------------------------------ #

def _logger_with(handler: RingBufferHandler, name: str) -> logging.Logger:
    lg = logging.getLogger(name)
    lg.handlers = [handler]
    lg.setLevel(logging.DEBUG)
    lg.propagate = False
    return lg


def test_emit_and_get_roundtrip():
    h = RingBufferHandler(capacity=10)
    lg = _logger_with(h, "test.lb.roundtrip")
    lg.info("hello world")
    logs = h.get()["logs"]
    assert len(logs) == 1
    assert logs[0]["msg"] == "hello world"
    assert logs[0]["level"] == "INFO"
    assert logs[0]["id"] == 1
    assert "time" in logs[0]


def test_incremental_after():
    h = RingBufferHandler(capacity=10)
    lg = _logger_with(h, "test.lb.after")
    lg.info("one")
    first = h.get()
    assert first["last_id"] == 1
    lg.info("two")
    nxt = h.get(after=first["last_id"])
    assert [l["msg"] for l in nxt["logs"]] == ["two"]
    assert nxt["last_id"] == 2


def test_level_filter():
    h = RingBufferHandler(capacity=10)
    lg = _logger_with(h, "test.lb.level")
    lg.debug("dbg")
    lg.info("inf")
    lg.error("err")
    only_warn_plus = [l["msg"] for l in h.get(level="WARNING")["logs"]]
    assert only_warn_plus == ["err"]


def test_contains_filter_matches_msg_and_name():
    h = RingBufferHandler(capacity=10)
    lg = _logger_with(h, "test.lb.contains.special")
    lg.info("archiving screenshot")
    lg.info("resolving titles")
    by_msg = [l["msg"] for l in h.get(contains="titles")["logs"]]
    assert by_msg == ["resolving titles"]
    # logger name is also searched
    by_name = h.get(contains="contains.special")["logs"]
    assert len(by_name) == 2


def test_capacity_is_bounded():
    h = RingBufferHandler(capacity=3)
    lg = _logger_with(h, "test.lb.cap")
    for i in range(6):
        lg.info("line %d", i)
    logs = h.get()["logs"]
    assert len(logs) == 3
    assert [l["msg"] for l in logs] == ["line 3", "line 4", "line 5"]


def test_uvicorn_access_excluded():
    h = RingBufferHandler(capacity=10)
    rec = logging.LogRecord(
        "uvicorn.access", logging.INFO, __file__, 1, "GET /api/logs", None, None
    )
    h.emit(rec)
    assert h.get()["logs"] == []


def test_exception_traceback_captured():
    h = RingBufferHandler(capacity=10)
    lg = _logger_with(h, "test.lb.exc")
    try:
        raise ValueError("boom")
    except ValueError:
        lg.exception("failed")
    msg = h.get()["logs"][0]["msg"]
    assert "boom" in msg and "Traceback" in msg


def test_last_id_advances_when_newest_filtered_out():
    h = RingBufferHandler(capacity=10)
    lg = _logger_with(h, "test.lb.lastid")
    lg.info("alpha")
    lg.info("beta")
    res = h.get(after=0, contains="no-such-text")
    assert res["logs"] == []
    assert res["last_id"] == 2  # still advances past filtered lines


# ------------------------------------------------------------------ #
# Endpoint tests                                                      #
# ------------------------------------------------------------------ #

@pytest.fixture
def client():
    from xteink_service.koreader_sync import app
    return TestClient(app)


def test_logs_page_served(client):
    r = client.get("/logs")
    assert r.status_code == 200
    assert "text/html" in r.headers["content-type"]
    assert 'id="log"' in r.text
    assert "/api/logs" in r.text


def test_api_logs_captures_root_logging(client):
    logbuffer.install()  # idempotent; attaches to root
    marker = "MARKER_UNIQUE_LB_9c3f"
    logging.getLogger("xteink_service.testcapture").info(marker)
    r = client.get("/api/logs", params={"contains": marker})
    assert r.status_code == 200
    body = r.json()
    assert any(marker in line["msg"] for line in body["logs"])
    assert body["last_id"] >= 1


def test_api_device(client, monkeypatch):
    import xteink_service.api as api_mod

    async def fake_probe(*_a, **_k):
        return True

    monkeypatch.setattr(api_mod, "_probe_device", fake_probe)
    r = client.get("/api/device")
    assert r.status_code == 200
    data = r.json()
    assert data["online"] is True
    assert "host" in data
