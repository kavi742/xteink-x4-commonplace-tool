"""
KOReader sync server — receives reading progress from the X4.
Stores updates in SQLite; vault writes happen in Phase 7.

Endpoints
---------
POST /syncs/progress          — KOReader sends progress update
PUT  /syncs/progress          — same (some KOReader versions use PUT)
GET  /syncs/progress/{doc}    — KOReader queries last known position
POST /users/create            — KOReader auth handshake (always succeeds)
GET  /users/auth              — KOReader auth check   (always succeeds)
"""
import os
import sqlite3
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()


# ------------------------------------------------------------------ #
# Storage                                                              #
# ------------------------------------------------------------------ #

class ProgressStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
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
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO progress_updates
                   (document, progress, percentage, device, device_id, title, author, timestamp)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (document, progress, percentage, device, device_id, title, author, ts),
            )
        return self._latest(document)

    def _latest(self, document: str) -> dict | None:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM progress_updates WHERE document=? ORDER BY id DESC LIMIT 1",
                (document,),
            ).fetchone()
        return dict(row) if row else None

    def all(self) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
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
    return record


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
