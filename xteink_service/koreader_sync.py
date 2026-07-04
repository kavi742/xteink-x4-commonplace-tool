"""
KOReader sync server — receives reading progress from the X4.
Stores updates in SQLite and writes to the Obsidian vault.

Endpoints
---------
POST /syncs/progress          — KOReader sends progress update
PUT  /syncs/progress          — same (some KOReader versions use PUT)
GET  /syncs/progress/{doc}    — KOReader queries last known position
POST /users/create            — KOReader auth handshake (always succeeds)
GET  /users/auth              — KOReader auth check   (always succeeds)
"""
import asyncio
import logging
import os
import sqlite3
from datetime import date as DateType
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel

logger = logging.getLogger(__name__)

app = FastAPI()


# ------------------------------------------------------------------ #
# Storage                                                              #
# ------------------------------------------------------------------ #

class ProgressStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        # check_same_thread=False is safe here: FastAPI uses a single thread
        # per request under the default uvicorn worker, and we never mutate
        # from multiple threads simultaneously.
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._init_db()

    def _init_db(self) -> None:
        self._conn.execute("""
                CREATE TABLE IF NOT EXISTS progress_updates (
                    id          INTEGER PRIMARY KEY AUTOINCREMENT,
                    document    TEXT    NOT NULL,
                    progress    TEXT,
                    percentage  REAL,
                    device      TEXT,
                    device_id   TEXT,
                    title       TEXT,
                    author      TEXT,
                    timestamp   INTEGER DEFAULT (strftime('%s','now'))
                )
            """)
        self._conn.commit()

    def upsert(
        self,
        document: str,
        progress: str,
        percentage: float,
        device: str = "",
        device_id: str = "",
        title: str = "",
        author: str = "",
    ) -> dict:
        ts = int(datetime.now(timezone.utc).timestamp())
        self._conn.execute(
            """INSERT INTO progress_updates
               (document, progress, percentage, device, device_id, title, author, timestamp)
               VALUES (?,?,?,?,?,?,?,?)""",
            (document, progress, percentage, device, device_id, title, author, ts),
        )
        self._conn.commit()
        return self._latest(document)

    def _latest(self, document: str) -> dict | None:
        self._conn.row_factory = sqlite3.Row
        row = self._conn.execute(
            "SELECT * FROM progress_updates WHERE document=? ORDER BY id DESC LIMIT 1",
            (document,),
        ).fetchone()
        return dict(row) if row else None

    def _latest_n(self, document: str, n: int) -> list[dict]:
        self._conn.row_factory = sqlite3.Row
        rows = self._conn.execute(
            "SELECT * FROM progress_updates WHERE document=? ORDER BY id DESC LIMIT ?",
            (document, n),
        ).fetchall()
        return [dict(r) for r in rows]

    def all(self) -> list[dict]:
        self._conn.row_factory = sqlite3.Row
        rows = self._conn.execute(
            "SELECT * FROM progress_updates ORDER BY id DESC LIMIT 200"
        ).fetchall()
        return [dict(r) for r in rows]


_store = ProgressStore(
    os.getenv("KOREADER_DB", "/tmp/koreader.db")
)


# ------------------------------------------------------------------ #
# Request / response models                                           #
# ------------------------------------------------------------------ #

class ProgressIn(BaseModel):
    # KOReader kosync fields
    document:   str
    progress:   str = "0"
    percentage: float = 0.0
    device:     str = ""
    device_id:  str = ""
    # Optional metadata (CrossPoint / custom clients)
    title:  Optional[str] = None
    author: Optional[str] = None


# ------------------------------------------------------------------ #
# Auth stubs (KOReader requires these endpoints to exist)             #
# ------------------------------------------------------------------ #

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/users/create")
@app.get("/users/auth")
async def auth_stub():
    return {"authorized": "OK"}


# ------------------------------------------------------------------ #
# Progress sync                                                        #
# ------------------------------------------------------------------ #

@app.post("/syncs/progress")
@app.put("/syncs/progress")
async def put_progress(update: ProgressIn):
    """Receive a reading position from KOReader and store it."""
    record = _store.upsert(
        document=update.document,
        progress=update.progress,
        percentage=update.percentage,
        device=update.device,
        device_id=update.device_id,
        title=update.title or "",
        author=update.author or "",
    )
    await _write_progress_to_vault(update)
    return record


async def _try_scan_resolve(doc_hash: str, state_db: str) -> str | None:
    """Attempt to resolve an unknown hash by scanning epub filenames on the device."""
    import hashlib
    import socket
    import aiohttp as _aiohttp
    device_host = os.getenv("DEVICE_HOST", "crosspoint.local")
    try:
        # Resolve mDNS hostname synchronously (uses avahi/nsswitch) before async HTTP
        device_ip = await asyncio.get_event_loop().run_in_executor(
            None, socket.gethostbyname, device_host
        )
        from xteink_service.alias import _list_device_books, _state_conn
        timeout = _aiohttp.ClientTimeout(total=15)
        async with _aiohttp.ClientSession(timeout=timeout) as session:
            books = await _list_device_books(session, device_ip)
        for book in books:
            name = book["name"]
            if hashlib.md5(name.encode()).hexdigest() == doc_hash:
                title = name.rsplit(".", 1)[0]
                conn = _state_conn(state_db)
                conn.execute(
                    "INSERT OR REPLACE INTO document_aliases "
                    "(hash, title, filename, resolved_by, computed_at) VALUES (?,?,?,'auto',?)",
                    (doc_hash, title, name, datetime.now(timezone.utc).isoformat()),
                )
                conn.commit()
                logger.info("Auto-resolved %s… \u2192 %s", doc_hash[:8], title)
                return title
    except Exception as exc:
        logger.debug("Auto-scan failed: %s", exc)
    return None


async def _write_progress_to_vault(update: ProgressIn) -> None:
    """Write reading progress to vault markdown. Silently skips if vault not configured."""
    vault_path = os.getenv("VAULT_PATH", "/data/vault")
    if not os.path.isdir(vault_path):
        return

    # Resolve title: alias table → request metadata → auto-scan device → give up
    title = update.title or None
    state_db = os.getenv("STATE_DB", "/data/state/state.db")
    try:
        from xteink_service.state import SyncState
        title = SyncState(state_db).get_title(update.document) or title
    except Exception:
        pass
    if not title:
        title = await _try_scan_resolve(update.document, state_db)
    if not title:
        logger.debug("No title for %s — skipping vault write", update.document[:16])
        return

    try:
        from xteink_service.vault_writer import VaultWriter
        today = DateType.today()
        today_str = today.isoformat()
        pct = update.percentage * 100

        # Collect context from previous records (history[0] = just inserted)
        history = _store._latest_n(update.document, 50)
        older = history[1:]
        today_older = [r for r in older
                       if DateType.fromtimestamp(r["timestamp"]).isoformat() == today_str]
        prev_records = [r for r in older
                        if DateType.fromtimestamp(r["timestamp"]).isoformat() != today_str]

        first_today_pct = (today_older[-1]["percentage"] * 100) if today_older else None
        prev_pct  = (prev_records[0]["percentage"] * 100) if prev_records else None
        prev_day  = (DateType.fromtimestamp(prev_records[0]["timestamp"])
                     if prev_records else None)

        vw = VaultWriter(vault_path)
        vw.write_reading_log(today, title, pct,
                             progress=update.progress,
                             prev_percentage=prev_pct,
                             prev_day=prev_day)
        vw.update_book_timeline(title, update.author or "", today, pct,
                                progress=update.progress,
                                first_today_pct=first_today_pct)
        logger.info("Vault: wrote progress for %s (%.1f%%)", title, pct)
    except Exception as exc:
        logger.warning("Vault write failed for %s: %s", title, exc)


@app.get("/syncs/progress/{document:path}")
async def get_progress(document: str):
    """Return the last known position for a document."""
    record = _store._latest(document)
    if record is None:
        return {}
    return record


@app.get("/syncs/progress")
async def list_progress():
    """List recent progress updates (admin / debug)."""
    return _store.all()
