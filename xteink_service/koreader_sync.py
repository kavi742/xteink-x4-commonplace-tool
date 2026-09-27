"""
KOReader sync server — receives reading progress from any KOReader client.
Stores updates in SQLite and writes to the Obsidian vault.

Any device running KOReader's Progress sync plugin can point here: the X4
(CrossPoint), and a Kindle running KOReader (e.g. with kindle.koplugin, which
makes Kindle-native books readable in KOReader). The protocol is identical
kosync, so devices are distinguished by the `device` / `device_id` fields they
send and tracked in the `devices` table.

Book titles come from the optional `metadata` payload a device sends when
"Send Metadata" is enabled (CrossPoint: Settings > System > KOReader Sync;
KOReader: Menu > Tools > Progress sync). Both are off by default. With it on,
no title has to be recovered from the document digest.

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
import json
import logging
import hashlib
import os
import secrets
import sqlite3
from datetime import date as DateType
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, status
from pydantic import BaseModel, ConfigDict

logger = logging.getLogger(__name__)

app = FastAPI(title="xteink-service", version="1.0.0")

# Mount Phase 8+9 API endpoints
from xteink_service.api import router as api_router  # noqa: E402
app.include_router(api_router)


# ------------------------------------------------------------------ #
# Public exposure guard                                                #
# ------------------------------------------------------------------ #
# When PUBLIC_SYNC_HOST is set (the DuckDNS domain that fronts KOReader sync via
# NPM), requests arriving on that hostname may ONLY reach the kosync endpoints —
# the web UI and CRUD API stay private (LAN / Tailscale). Requests on any other
# Host (localhost, ghostbird, the Tailscale IP, …) are unaffected, so the full UI
# still works at home. This is what keeps the UI off the public internet while
# only the (x-auth-protected) sync endpoints are exposed.
_PUBLIC_SYNC_HOST = os.getenv("PUBLIC_SYNC_HOST", "").split(":")[0].lower()
_PUBLIC_ALLOWED = ("/syncs", "/users", "/health")


@app.middleware("http")
async def _restrict_public_sync_host(request, call_next):
    if _PUBLIC_SYNC_HOST:
        host = (
            request.headers.get("x-forwarded-host")
            or request.headers.get("host", "")
        ).split(":")[0].lower()
        if host == _PUBLIC_SYNC_HOST and not request.url.path.startswith(_PUBLIC_ALLOWED):
            from fastapi.responses import JSONResponse
            return JSONResponse({"detail": "Not Found"}, status_code=404)
    return await call_next(request)


# The built SvelteKit web UI is served from the site root (/) at the bottom of
# this module (see the SPA fallback). The app is built with an empty base path
# — assets are referenced from /_app and links are root-absolute (/books, …) —
# so it must be served from / rather than a sub-path, or every asset 404s and
# the page renders blank.
import pathlib as _pathlib
_web_build = _pathlib.Path(__file__).parent.parent / "web" / "build"

# ------------------------------------------------------------------ #
# KOReader kosync auth                                                 #
# ------------------------------------------------------------------ #
# KOReader's progress-sync client authenticates with the kosync scheme: it sends
# `x-auth-user` and `x-auth-key` headers, where the key is md5(password). It does
# NOT do HTTP Basic Auth — putting Basic Auth in front (here or at NPM) breaks
# sync with 401. When KOSYNC_USER + KOSYNC_PASSWORD are set, every kosync
# endpoint requires a matching user + key; unset, the endpoints stay open (the
# LAN-only default). Set these when exposing the server publicly (e.g. DuckDNS).
_KOSYNC_USER = os.getenv("KOSYNC_USER", "")
_KOSYNC_PASS = os.getenv("KOSYNC_PASSWORD", "")
# A pre-hashed key (KOSYNC_MD5) may be supplied instead of the plaintext password.
_KOSYNC_KEY = (
    os.getenv("KOSYNC_MD5", "")
    or (hashlib.md5(_KOSYNC_PASS.encode()).hexdigest() if _KOSYNC_PASS else "")
).lower()
_KOSYNC_REQUIRED = bool(_KOSYNC_USER and _KOSYNC_KEY)


def _require_kosync_auth(
    x_auth_user: Annotated[str | None, Header()] = None,
    x_auth_key: Annotated[str | None, Header()] = None,
) -> None:
    """Validate KOReader's x-auth-user / x-auth-key (md5 of the password) headers.

    No-op unless KOSYNC_USER + KOSYNC_PASSWORD (or KOSYNC_MD5) are configured.
    """
    if not _KOSYNC_REQUIRED:
        return
    if not x_auth_user or not x_auth_key:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Missing kosync credentials")
    ok = (
        secrets.compare_digest(x_auth_user, _KOSYNC_USER)
        and secrets.compare_digest(x_auth_key.lower(), _KOSYNC_KEY)
    )
    if not ok:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, detail="Invalid kosync credentials")


KosyncAuth = Annotated[None, Depends(_require_kosync_auth)]


# ------------------------------------------------------------------ #
# Storage                                                              #
# ------------------------------------------------------------------ #

class ProgressStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._initialized = False

    def _connect(self) -> sqlite3.Connection:
        """Open a fresh connection, creating the schema on first use. Each call
        gets its own connection so a deleted/replaced DB file is picked up
        automatically on the next operation. Schema init is deferred out of
        __init__ so importing this module never touches the filesystem."""
        if not self._initialized:
            self._init_db()
            self._initialized = True
        return sqlite3.connect(self.db_path)

    def _init_db(self) -> None:
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
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
            conn.execute("""
                CREATE TABLE IF NOT EXISTS devices (
                    device_id   TEXT PRIMARY KEY,
                    device      TEXT    DEFAULT '',
                    first_seen  INTEGER,
                    last_seen   INTEGER
                )
            """)
            # Backfill devices seen before the table existed.
            conn.execute("""
                INSERT OR IGNORE INTO devices (device_id, device, first_seen, last_seen)
                SELECT COALESCE(NULLIF(device_id,''), device), device,
                       MIN(timestamp), MAX(timestamp)
                  FROM progress_updates
                 WHERE COALESCE(NULLIF(device_id,''), device) IS NOT NULL
                   AND COALESCE(NULLIF(device_id,''), device) != ''
                 GROUP BY COALESCE(NULLIF(device_id,''), device)
            """)
            try:
                conn.execute("ALTER TABLE progress_updates ADD COLUMN extra TEXT")
            except sqlite3.OperationalError:
                pass  # column already exists

    def record_device(self, device: str, device_id: str) -> None:
        """Register a syncing client. Called before the de-dupe check so a device
        that only ever re-sends a position it already reported is still known."""
        if not device_id and not device:
            return
        ts = int(datetime.now(timezone.utc).timestamp())
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO devices (device_id, device, first_seen, last_seen)
                   VALUES (?,?,?,?)
                   ON CONFLICT(device_id) DO UPDATE SET
                       device    = excluded.device,
                       last_seen = excluded.last_seen""",
                (device_id or device, device, ts, ts),
            )

    def devices(self) -> list[dict]:
        """Known sync clients, most recently seen first, with update counts."""
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                """SELECT d.device_id, d.device, d.first_seen, d.last_seen,
                          (SELECT COUNT(*) FROM progress_updates p
                            WHERE p.device_id = d.device_id) AS update_count,
                          (SELECT COUNT(DISTINCT p.document) FROM progress_updates p
                            WHERE p.device_id = d.device_id) AS book_count
                     FROM devices d ORDER BY d.last_seen DESC"""
            ).fetchall()
        return [dict(r) for r in rows]

    def devices_for_document(self, document: str) -> list[str]:
        """Distinct device names that have ever synced this document."""
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT DISTINCT device FROM progress_updates "
                "WHERE document = ? AND device != '' AND device IS NOT NULL",
                (document,),
            ).fetchall()
        return [r[0] for r in rows]

    def latest_across(self, documents: list[str]) -> dict | None:
        """Newest record among several document hashes for the same book."""
        if not documents:
            return None
        placeholders = ",".join("?" * len(documents))
        with self._connect() as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                f"SELECT * FROM progress_updates WHERE document IN ({placeholders}) "
                "ORDER BY timestamp DESC, id DESC LIMIT 1",
                documents,
            ).fetchone()
        return dict(row) if row else None

    def upsert(
        self,
        document: str,
        progress: str,
        percentage: float,
        device: str = "",
        device_id: str = "",
        title: str = "",
        author: str = "",
        extra: str | None = None,
    ) -> dict:
        ts = int(datetime.now(timezone.utc).timestamp())
        with self._connect() as conn:
            conn.execute(
                """INSERT INTO progress_updates
                   (document, progress, percentage, device, device_id, title, author, extra, timestamp)
                   VALUES (?,?,?,?,?,?,?,?,?)""",
                (document, progress, percentage, device, device_id, title, author, extra, ts),
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
    os.getenv("KOREADER_DB", "/data/state/koreader.db")
)

# Only the X4 exposes a file-listing API, so only hashes it reports can be
# resolved to titles without the device's help, by scanning during File
# Transfer. Every client can instead volunteer its own title — see
# DocumentMetadata.
_SCANNABLE_DEVICE_IDS = {"crosspoint-reader"}


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

class DocumentMetadata(BaseModel):
    """The optional `metadata` payload, sent when the device has "Send Metadata"
    enabled (off by default on both CrossPoint and KOReader).

    CrossPoint: Settings > System > KOReader Sync > Send Metadata.
    KOReader:   Menu > Tools > Progress sync > Send document metadata.

    The official kosync server ignores it. For us it is what removes the
    hash-matching problem: the device names its own book, so no title has to be
    recovered from the digest.
    """
    filename: Optional[str] = None
    title:    Optional[str] = None
    authors:  Optional[str | list[str]] = None

    def author_str(self) -> str:
        if isinstance(self.authors, list):
            return ", ".join(a for a in self.authors if a)
        # KOReader joins multiple authors with newlines.
        return ", ".join(p.strip() for p in (self.authors or "").splitlines() if p.strip())


class ProgressIn(BaseModel):
    # Unknown fields are kept rather than dropped: CrossPoint and KOReader both
    # add payload fields over time, and silently discarding them hides them.
    model_config = ConfigDict(extra="allow")

    # KOReader kosync fields
    document:   str
    progress:   str = "0"
    percentage: float = 0.0
    device:     str = ""
    device_id:  str = ""
    metadata:   Optional[DocumentMetadata] = None
    # Optional flat metadata (CrossPoint / custom clients)
    title:  Optional[str] = None
    author: Optional[str] = None

    def resolved_title(self) -> str:
        """Best title the client gave us, if any."""
        md = self.metadata
        if md:
            if md.title:
                return md.title.strip()
            if md.filename:
                return md.filename.rsplit(".", 1)[0].strip()
        return (self.title or "").strip()

    def resolved_author(self) -> str:
        return ((self.metadata.author_str() if self.metadata else "")
                or (self.author or "").strip())


# ------------------------------------------------------------------ #
# Auth stubs (KOReader requires these endpoints to exist)             #
# ------------------------------------------------------------------ #

@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/users/create")
@app.get("/users/auth")
async def auth_stub(_: KosyncAuth):
    return {"authorized": "OK"}


# ------------------------------------------------------------------ #
# Progress sync                                                        #
# ------------------------------------------------------------------ #

def _kosync_view(rec: dict | None) -> dict:
    """Project a stored record to the standard kosync response fields only.

    KOReader / CrossPoint's sync client expects the kosync schema; our extra
    columns (id, title, author) can trip its JSON parser, so strip them.
    Returns {} for a missing record.
    """
    if not rec:
        return {}
    return {
        "document": rec.get("document", ""),
        "progress": rec.get("progress", ""),
        "percentage": rec.get("percentage", 0.0),
        "device": rec.get("device", ""),
        "device_id": rec.get("device_id", ""),
        "timestamp": rec.get("timestamp"),
    }


@app.post("/syncs/progress")
@app.put("/syncs/progress")
async def put_progress(update: ProgressIn, _: KosyncAuth):
    """Receive a reading position from KOReader and store it."""
    # Registered before the de-dupe check below, so a device whose position is
    # already known (e.g. it just pulled it from here) is still recorded.
    _store.record_device(update.device, update.device_id)
    _record_client_title(update)
    extra = _log_unmodelled_fields(update)

    # De-dupe: KOReader re-syncs the same position periodically. If nothing has
    # moved since the last sync for this document, don't log another identical
    # reading-log entry (or re-write the vault).
    last = _store._latest(update.document)
    if last and last.get("progress") == update.progress \
            and last.get("percentage") == update.percentage:
        return _kosync_view(last)

    record = _store.upsert(
        document=update.document,
        progress=update.progress,
        percentage=update.percentage,
        device=update.device,
        device_id=update.device_id,
        title=update.resolved_title(),
        author=update.resolved_author(),
        extra=extra,
    )
    await _write_progress_to_vault(update)
    return _kosync_view(record)


_seen_extra_shapes: set[tuple[str, tuple[str, ...]]] = set()


def _log_unmodelled_fields(update: ProgressIn) -> str | None:
    """Record payload fields the kosync schema doesn't define.

    CrossPoint and KOReader both extend the payload between releases; logging
    the first occurrence of each new shape is how we notice.
    """
    fields = update.model_extra
    if not fields:
        return None
    shape = (update.device_id or update.device, tuple(sorted(fields)))
    if shape not in _seen_extra_shapes:
        _seen_extra_shapes.add(shape)
        logger.info("Extra kosync fields from %s: %s",
                    update.device or "unknown device", json.dumps(fields, default=str))
    return json.dumps(fields, default=str)


def _record_client_title(update: ProgressIn) -> None:
    """Persist a title the client volunteered so the hash resolves from now on.

    For devices we can't scan (a Kindle running KOReader) this is the only
    automatic source of a book title — enable "Send document metadata" under
    KOReader's Progress sync menu to turn it on.
    """
    title = update.resolved_title()
    if not title:
        return
    try:
        from xteink_service.state import SyncState
        SyncState(os.getenv("STATE_DB", "/data/state/state.db")).set_title(
            update.document, title,
            filename=(update.metadata.filename or "") if update.metadata else "",
            resolved_by="metadata",
        )
    except Exception as exc:
        logger.debug("Could not store client title for %s: %s",
                     update.document[:16], exc)


async def _write_progress_to_vault(update: ProgressIn) -> None:
    """Write reading progress to vault markdown. Silently skips if vault not configured."""
    vault_path = os.getenv("VAULT_PATH", "/data/vault")
    if not os.path.isdir(vault_path):
        return

    # Resolve title: alias table (incl. titles clients volunteered) → this
    # request's metadata → give up.
    # Note: the X4's port 80 is closed during KOReader sync (reading mode), so
    # auto-scan is not attempted here. Its aliases are resolved by sync_once.py
    # during File Transfer mode, when the file listing API is available.
    title = update.resolved_title() or None
    state_db = os.getenv("STATE_DB", "/data/state/state.db")
    try:
        from xteink_service.state import SyncState
        title = SyncState(state_db).get_title(update.document) or title
    except Exception:
        pass
    if not title:
        hint = "turn on Send Metadata on the device, or map it at /aliases"
        if update.device_id in _SCANNABLE_DEVICE_IDS:
            hint += " (or run sync_once in File Transfer mode)"
        logger.info(
            "No title for %s (from %s) — skipping vault write; %s",
            update.document[:16], update.device or "unknown device", hint,
        )
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

        # Only label the device once a book has actually been read on more than
        # one — a single-device library shouldn't carry noise in every line.
        device = update.device if len(_store.devices_for_document(update.document)) > 1 else None

        vw = VaultWriter(vault_path)
        vw.write_reading_log(today, title, pct,
                             progress=update.progress,
                             prev_percentage=prev_pct,
                             prev_day=prev_day,
                             device=device)
        vw.update_book_timeline(title, update.resolved_author(), today, pct,
                                progress=update.progress,
                                first_today_pct=first_today_pct,
                                device=device)
        logger.info("Vault: wrote progress for %s (%.1f%%) from %s",
                    title, pct, update.device or "unknown device")
        _notify(f"Reading: {title} — {pct:.1f}%")
    except Exception as exc:
        logger.warning("Vault write failed for %s: %s", title, exc)


@app.get("/syncs/progress/{document:path}")
async def get_progress(document: str, _: KosyncAuth):
    """Return the last known position for a document (kosync fields only).

    Devices only sync with each other when their document digests match, and two
    devices holding different copies of a book never produce the same digest.
    So the newest position is taken across every hash that resolves to the same
    book — see `_linked_documents`.
    """
    linked = _linked_documents(document)
    record = _store.latest_across(linked) if linked else _store._latest(document)
    if record and record["document"] != document:
        logger.info("Cross-device sync: serving %s's position for %s to a client asking for %s",
                    record["device"] or "?", record["document"][:12], document[:12])
        # Echo the requested hash: the client asked about its own copy.
        record = {**record, "document": document}
    return _kosync_view(record)


def _linked_documents(document: str) -> list[str]:
    """Every document hash that refers to the same book as `document`.

    Two devices reading the same book produce different digests unless they hold
    byte-identical files with identical names, so hashes are grouped by the book
    title they resolve to instead. Mapping a hash to a title (auto-scan, KOReader
    metadata, or by hand at /aliases) is therefore what links two devices.
    """
    try:
        state_db = os.getenv("STATE_DB", "/data/state/state.db")
        with sqlite3.connect(state_db) as conn:
            rows = conn.execute("SELECT hash, title FROM document_aliases").fetchall()
    except Exception:
        return []

    from xteink_service.booktitle import canonical_book_slug
    by_hash = {h: canonical_book_slug(t) for h, t in rows}
    slug = by_hash.get(document)
    if not slug:
        return []
    return [h for h, s in by_hash.items() if s == slug]


@app.get("/syncs/progress")
async def list_progress(_: KosyncAuth):
    """List recent progress updates (admin / debug)."""
    return _store.all()


# ------------------------------------------------------------------ #
# Web UI (SvelteKit SPA) — served at the site root                     #
# ------------------------------------------------------------------ #
# Registered LAST so every API route above (/status, /api/*, /syncs/*,
# /users/*, /health, /docs) is matched first. This block only serves the SPA
# shell, its hashed assets, and client-side routes (/books, /log, /tbr, …).
if _web_build.exists():
    from fastapi.staticfiles import StaticFiles
    from fastapi.responses import FileResponse, RedirectResponse

    # Immutable, content-hashed JS/CSS bundles.
    app.mount("/_app", StaticFiles(directory=str(_web_build / "_app")), name="assets")

    _web_root = _web_build.resolve()
    _index = _web_root / "index.html"
    # The SPA shell lives at a stable URL (/), so it must always be revalidated
    # — otherwise clients keep loading stale asset references after a redeploy
    # (the content-hashed /_app bundles are safe to cache forever by contrast).
    # no-cache = may store but must revalidate via etag (fast 304 if unchanged).
    _SHELL_HEADERS = {"Cache-Control": "no-cache"}

    @app.get("/app")
    @app.get("/app/{_rest:path}")
    async def _legacy_app_redirect(_rest: str = ""):
        """The UI used to live at /app; keep old bookmarks working."""
        return RedirectResponse("/", status_code=307)

    @app.get("/")
    async def _spa_root():
        return FileResponse(_index, headers=_SHELL_HEADERS)

    @app.get("/{full_path:path}")
    async def _spa_fallback(full_path: str):
        """Serve a real static file if one exists (robots.txt, etc.), else the
        SPA shell so client-side routes resolve on hard refresh."""
        if full_path:
            candidate = (_web_root / full_path).resolve()
            # Guard against path traversal outside the build directory.
            if candidate.is_file() and candidate.is_relative_to(_web_root):
                return FileResponse(candidate)
        return FileResponse(_index, headers=_SHELL_HEADERS)
