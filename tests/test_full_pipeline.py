"""
Full pipeline integration test.

Mock mode (default, no hardware):
    uv run pytest tests/test_full_pipeline.py -v -s

Live mode (X4 must be in File Transfer mode):
    uv run pytest tests/test_full_pipeline.py -v -s --live
"""
import asyncio
import hashlib
import io
import os
import sqlite3
import threading
from pathlib import Path

import pytest
from aiohttp import web
from PIL import Image

from xteink_service.archiver import ScreenshotArchiver
from xteink_service.alias import _scan_resolve
from xteink_service.koreader_sync import ProgressIn, _write_progress_to_vault

# ------------------------------------------------------------------ #
# Constants                                                            #
# ------------------------------------------------------------------ #

BOOK_FILENAME = "Fifteen Dogs - Andre Alexis.epub"
BOOK_HASH = hashlib.md5(BOOK_FILENAME.encode()).hexdigest()
MTIME = 1751500800


def _make_bmp(w: int = 480, h: int = 360) -> bytes:
    img = Image.new("RGB", (w, h), color=(240, 235, 220))
    buf = io.BytesIO()
    img.save(buf, format="BMP")
    return buf.getvalue()


BMP_DATA = _make_bmp()


# ------------------------------------------------------------------ #
# Mock X4 aiohttp server                                              #
# ------------------------------------------------------------------ #

async def _handle_status(_req):
    return web.json_response({"version": "1.4.0-mock", "mode": "STA",
                               "ip": "127.0.0.1", "device": "X4"})


async def _handle_files(req):
    path = req.rel_url.query.get("path", "")
    if path == "":
        return web.json_response([
            {"name": "Alexis, Andre", "isDirectory": True, "size": 0, "isEpub": False},
        ])
    if path == "/screenshots":
        return web.json_response([
            {"name": "Pastoral", "isDirectory": True, "size": 0, "isEpub": False},
        ])
    if path == "/screenshots/Pastoral":
        return web.json_response([
            {"name": "Pastoral_ch8_p25_20pct.bmp", "isDirectory": False,
             "size": len(BMP_DATA), "mtime": MTIME, "isEpub": False},
            {"name": "Pastoral_ch9_p50_50pct.bmp", "isDirectory": False,
             "size": len(BMP_DATA), "mtime": MTIME, "isEpub": False},
        ])
    if "Alexis" in path:
        return web.json_response([
            {"name": BOOK_FILENAME, "isDirectory": False, "size": 318907, "isEpub": True},
        ])
    return web.json_response([])


async def _handle_download(_req):
    return web.Response(body=BMP_DATA, content_type="application/octet-stream")


def _build_mock_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/api/status", _handle_status)
    app.router.add_get("/api/files",  _handle_files)
    app.router.add_get("/download",   _handle_download)
    return app


# ------------------------------------------------------------------ #
# Session-scoped fixtures                                              #
# ------------------------------------------------------------------ #

@pytest.fixture(scope="session")
def mock_device_host(live):
    if live:
        yield "crosspoint.local"
        return

    runner_holder: dict = {}

    async def _start():
        app = _build_mock_app()
        runner = web.AppRunner(app)
        await runner.setup()
        site = web.TCPSite(runner, "127.0.0.1", 0)
        await site.start()
        runner_holder["runner"] = runner
        runner_holder["port"] = runner.addresses[0][1]

    loop = asyncio.new_event_loop()
    loop.run_until_complete(_start())
    threading.Thread(target=loop.run_forever, daemon=True).start()

    yield f"127.0.0.1:{runner_holder['port']}"

    loop.call_soon_threadsafe(loop.stop)


@pytest.fixture(scope="session")
def pipeline_vault(tmp_path_factory) -> Path:
    return tmp_path_factory.mktemp("pipeline_vault")


@pytest.fixture(scope="session")
def pipeline_state_db(tmp_path_factory) -> str:
    return str(tmp_path_factory.mktemp("pipeline_state") / "state.db")


@pytest.fixture(scope="session")
def pipeline_koreader_db(tmp_path_factory) -> str:
    return str(tmp_path_factory.mktemp("pipeline_koreader") / "koreader.db")


# ------------------------------------------------------------------ #
# Pipeline tests (ordered by name prefix)                             #
# ------------------------------------------------------------------ #

async def test_1_sync_once_archives_screenshots(
    mock_device_host, pipeline_vault, pipeline_state_db
):
    """sync_once downloads BMPs, converts to PNG, writes vault + JSON sidecars."""
    archiver = ScreenshotArchiver(str(pipeline_vault), mock_device_host, pipeline_state_db)
    await archiver.run_sync()

    pngs  = list((pipeline_vault / "Books").rglob("*.png"))
    jsons = list((pipeline_vault / "Books").rglob("*.json"))
    mds   = list((pipeline_vault / "Books").rglob("*.md"))

    assert pngs,                    "No PNGs written to vault"
    assert len(jsons) == len(pngs), "JSON sidecar count mismatch"
    assert mds,                     "No book notes written"
    print(f"\n  {len(pngs)} PNG(s), {len(jsons)} sidecar(s), {len(mds)} note(s)")


async def test_2_alias_scan_resolves_hashes(
    mock_device_host, pipeline_state_db, pipeline_koreader_db
):
    """_scan_resolve maps md5(filename) hashes from device file listing."""
    with sqlite3.connect(pipeline_koreader_db) as conn:
        conn.execute("""CREATE TABLE IF NOT EXISTS progress_updates (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            document TEXT NOT NULL, progress TEXT, percentage REAL,
            device TEXT, device_id TEXT, title TEXT, author TEXT,
            timestamp INTEGER DEFAULT (strftime('%s','now'))
        )""")
        conn.execute(
            "INSERT INTO progress_updates (document, progress, percentage) VALUES (?,?,?)",
            (BOOK_HASH, "/body/DocFragment[5]/body", 0.25),
        )

    await _scan_resolve(pipeline_state_db, pipeline_koreader_db, mock_device_host)

    with sqlite3.connect(pipeline_state_db) as conn:
        row = conn.execute(
            "SELECT title FROM document_aliases WHERE hash = ?", (BOOK_HASH,)
        ).fetchone()

    assert row is not None, f"Hash {BOOK_HASH[:8]}... not resolved"
    assert "Fifteen Dogs" in row[0]
    print(f"\n  Resolved {BOOK_HASH[:8]}... → {row[0]}")


async def test_3_koreader_sync_writes_vault(
    pipeline_vault, pipeline_state_db, pipeline_koreader_db
):
    """Progress PUT stores record and writes reading log + book timeline."""
    os.environ["VAULT_PATH"]  = str(pipeline_vault)
    os.environ["STATE_DB"]    = pipeline_state_db
    os.environ["KOREADER_DB"] = pipeline_koreader_db

    update = ProgressIn(
        document=BOOK_HASH,
        progress="/body/DocFragment[5]/body",
        percentage=0.25,
        device="test-device",
    )
    await _write_progress_to_vault(update)

    log = pipeline_vault / "Reading Log.md"
    assert log.exists(), "Reading Log.md not created"
    content = log.read_text()
    assert "Fifteen Dogs" in content
    print(f"\n  Reading Log.md:\n{content}")


async def test_4_book_timeline_updated(pipeline_vault):
    """Book note has a reading progress line."""
    notes = list((pipeline_vault / "Books").glob("Fifteen Dogs*.md"))
    assert notes, f"No Fifteen Dogs book note in {pipeline_vault / 'Books'}"
    content = notes[0].read_text()
    assert "%" in content
    print(f"\n  {notes[0].name}:\n{content[-300:]}")


async def test_5_idempotency(mock_device_host, pipeline_vault, pipeline_state_db):
    """Second sync_once skips already-archived files — no duplicate PNGs."""
    pngs_before = list((pipeline_vault / "Books").rglob("*.png"))

    archiver = ScreenshotArchiver(str(pipeline_vault), mock_device_host, pipeline_state_db)
    await archiver.run_sync()

    pngs_after = list((pipeline_vault / "Books").rglob("*.png"))
    assert len(pngs_before) == len(pngs_after), \
        f"Second run added PNGs: {len(pngs_before)} → {len(pngs_after)}"
    print(f"\n  Idempotency OK: {len(pngs_after)} PNG(s), no duplicates")
