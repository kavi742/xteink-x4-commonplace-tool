"""
KOReader sync server — receives reading progress from the X4.
Stores updates in SQLite and writes to the Obsidian vault.

Endpoints
---------
POST /syncs/progress          — KOReader sends progress update
PUT  /syncs/progress          — same (some KOReader versions use PUT)
GET  /syncs/progress/{doc}    — KOReader queries last known position
POST /users/create            — KOReader auth handshake
GET  /users/auth              — KOReader auth check

Authentication
--------------
Set SYNC_USER and SYNC_PASSWORD env vars to require HTTP Basic Auth.
Leave them unset to disable auth (e.g. when Tailscale is the only gate).
"""
import asyncio
import logging
import os
import secrets
import sqlite3
from datetime import date as DateType
from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import Depends, FastAPI, HTTPException, status
from fastapi.security import HTTPBasic, HTTPBasicCredentials
from pydantic import BaseModel

logger = logging.getLogger(__name__)

app = FastAPI(title="xteink-service", version="1.0.0")

# Mount Phase 8+9 API endpoints
from xteink_service.api import router as api_router  # noqa: E402
app.include_router(api_router)

# Serve the built SvelteKit web UI at /app (production)
import pathlib as _pathlib
_web_build = _pathlib.Path(__file__).parent.parent / "web" / "build"
if _web_build.exists():
    from fastapi.staticfiles import StaticFiles
    app.mount("/app", StaticFiles(directory=str(_web_build), html=True), name="webapp")

_basic = HTTPBasic(auto_error=False)

_SYNC_USER = os.getenv("SYNC_USER", "")
_SYNC_PASS = os.getenv("SYNC_PASSWORD", "")
_AUTH_REQUIRED = bool(_SYNC_USER and _SYNC_PASS)


def _require_auth(creds: Annotated[HTTPBasicCredentials | None, Depends(_basic)]) -> None:
    """Validate Basic Auth when SYNC_USER/SYNC_PASSWORD are configured."""
    if not _AUTH_REQUIRED:
        return
    if creds is None:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            headers={"WWW-Authenticate": "Basic"})
    ok = (
        secrets.compare_digest(creds.username.encode(), _SYNC_USER.encode())
        and secrets.compare_digest(creds.password.encode(), _SYNC_PASS.encode())
    )
    if not ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED,
                            headers={"WWW-Authenticate": "Basic"})


Auth = Annotated[None, Depends(_require_auth)]


# ------------------------------------------------------------------ #
# Storage                                                              #
# ------------------------------------------------------------------ #

class ProgressStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _connect(self) -> sqlite3.Connection:
        """Open a fresh connection. Each call gets its own connection so a
        deleted/replaced DB file is picked up automatically on next operation."""
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        with self._connect() as conn:
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
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO progress_updates
                   (document, progress, percentage, device, device_id, title, author, timestamp)
                   VALUES (?,?,?,?,?,?,?,?)""",
                (document, progress, percentage, device, device_id, title, author, ts),
            )
        return self._latest(document)

    def _latest(self, document: str) -> dict | None:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT * FROM progress_updates WHERE document=? ORDER BY id DESC LIMIT 1",
                (document,),
            ).fetchone()
        return dict(row) if row else None

    def _latest_n(self, document: str, n: int) -> list[dict]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM progress_updates WHERE document=? ORDER BY id DESC LIMIT ?",
                (document, n),
            ).fetchall()
        return [dict(r) for r in rows]

    def all(self) -> list[dict]:
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM progress_updates ORDER BY id DESC LIMIT 200"
            ).fetchall()
        return [dict(r) for r in rows]


_store = ProgressStore(
    os.getenv("KOREADER_DB", "/tmp/koreader.db")
)


# ------------------------------------------------------------------ #
# ntfy.sh notifications                                               #
# ------------------------------------------------------------------ #

def _notify(message: str, title: str = "xteink-service") -> None:
    """
    Send a push notification via ntfy.sh (or a self-hosted ntfy server).
    Set NTFY_TOPIC env var to enable — silently skipped if not configured.
    Example: NTFY_TOPIC=https://ntfy.sh/my-xteink-topic
    """
    topic = os.getenv("NTFY_TOPIC", "")
    if not topic:
        return
    try:
        import urllib.request
        req = urllib.request.Request(
            topic,
            data=message.encode(),
            headers={"Title": title, "Content-Type": "text/plain"},
            method="POST",
        )
        urllib.request.urlopen(req, timeout=5)
    except Exception as exc:
        logger.debug("ntfy notification failed: %s", exc)


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
async def auth_stub(_: Auth):
    return {"authorized": "OK"}


# ------------------------------------------------------------------ #
# Progress sync                                                        #
# ------------------------------------------------------------------ #

@app.post("/syncs/progress")
@app.put("/syncs/progress")
async def put_progress(update: ProgressIn, _: Auth):
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


async def _write_progress_to_vault(update: ProgressIn) -> None:
    """Write reading progress to vault markdown. Silently skips if vault not configured."""
    vault_path = os.getenv("VAULT_PATH", "/data/vault")
    if not os.path.isdir(vault_path):
        return

    # Resolve title: alias table → request metadata → give up
    # Note: device port 80 is closed during KOReader sync (reading mode), so
    # auto-scan is not attempted here. Aliases are resolved by sync_once.py
    # which runs during File Transfer mode when the file listing API is available.
    title = update.title or None
    state_db = os.getenv("STATE_DB", "/data/state/state.db")
    try:
        from xteink_service.state import SyncState
        title = SyncState(state_db).get_title(update.document) or title
    except Exception:
        pass
    if not title:
        logger.debug("No title for %s — skipping vault write (run sync_once to resolve)", update.document[:16])
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
        _notify(f"Reading: {title} — {pct:.1f}%")
    except Exception as exc:
        logger.warning("Vault write failed for %s: %s", title, exc)


@app.get("/syncs/progress/{document:path}")
async def get_progress(document: str, _: Auth):
    """Return the last known position for a document."""
    record = _store._latest(document)
    if record is None:
        return {}
    return record


@app.get("/syncs/progress")
async def list_progress(_: Auth):
    """List recent progress updates (admin / debug)."""
    return _store.all()
