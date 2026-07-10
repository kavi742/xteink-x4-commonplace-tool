# Implementation Guide

## Project Structure

```
xteink-x4-commonplace-tool/
├── xteink_service/
│   ├── __init__.py
│   ├── __main__.py             # `python -m xteink_service` entry point
│   ├── main.py                 # Orchestrates watcher loop + KOReader server
│   ├── watcher.py              # Device poll loop
│   ├── archiver.py             # Screenshot download, BMP→PNG, OCR, vault write
│   ├── status_display.py       # WebSocket connection to X4 port 81
│   ├── koreader_sync.py        # KOReader kosync server (FastAPI) + ntfy notifications
│   ├── api.py                  # CRUD + status REST API (mounted on the sync app)
│   ├── vault_writer.py         # Obsidian vault file operations
│   ├── state.py                # SQLite state (dedup + aliases + highlights + TBR)
│   ├── alias.py                # Resolve KOReader hashes → book titles
│   ├── document_id.py          # CrossPoint partial-MD5 hash algorithm
│   ├── hash_books.py           # Browse device books, compute hashes
│   ├── sync_once.py            # One-shot sync cycle (CLI / Docker)
│   └── capture.py              # Single-screenshot capture test
├── web/                        # SvelteKit web UI (built to web/build, served at /app)
├── tests/
├── pyproject.toml              # Dependencies (managed with uv; locked in uv.lock)
├── Dockerfile
├── docker-compose.yml
├── README.md
├── ARCHITECTURE.md
├── PROJECT_SPEC.md
└── TODO.md
```

## Dependencies

Managed with [uv](https://docs.astral.sh/uv/) via `pyproject.toml` (locked in
`uv.lock`) — there is no `requirements.txt`:

```toml
# pyproject.toml  [project].dependencies
aiohttp>=3.9
websockets>=12.0
pillow>=10.0
pytesseract>=0.3.10   # calls the tesseract-ocr system binary (installed in the image)
fastapi>=0.110
uvicorn[standard]>=0.29
aiosqlite>=0.20
```

`pydantic` arrives transitively with FastAPI. Dev tools (`pytest`,
`pytest-asyncio`, `httpx`, `ruff`) live in `[dependency-groups].dev`.

## Core Modules

### 0. Device Discovery (`watcher.py`)

```python
import asyncio
import aiohttp

async def poll_for_device(host: str = "crosspoint.local", interval: int = 5) -> str:
    """Poll /api/status until the X4 responds, then return host."""
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                timeout = aiohttp.ClientTimeout(total=3)
                async with session.get(f"http://{host}/api/status", timeout=timeout) as resp:
                    if resp.status == 200:
                        return host
            except Exception:
                pass
            await asyncio.sleep(interval)

async def wait_for_offline(host: str, interval: int = 5) -> None:
    """Block until the X4 stops responding, so the next poll cycle is a fresh trigger."""
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                timeout = aiohttp.ClientTimeout(total=3)
                async with session.get(f"http://{host}/api/status", timeout=timeout) as resp:
                    if resp.status != 200:
                        return
            except Exception:
                return  # unreachable = offline
            await asyncio.sleep(interval)
```

### 1. Status Display (`status_display.py`)

```python
import contextlib
import websockets

@contextlib.asynccontextmanager
async def x4_status(host: str):
    """
    Async context manager: connect to the X4 status WebSocket (port 81).
    Yields a show(message) callable; no-ops gracefully if connection fails.
    """
    ws = None
    try:
        ws = await websockets.connect(f"ws://{host}:81/")
        async def show(message: str) -> None:
            try:
                await ws.send(f"START:{message}:1:/")
                if await ws.recv() == "READY":
                    await ws.send(b"X")
                    await ws.recv()  # drain DONE / PROGRESS:1:1
            except Exception:
                pass
        yield show
    except Exception:
        async def _noop(_: str) -> None:
            pass
        yield _noop  # connection failed — noop silently
    finally:
        if ws:
            await ws.close()
```

### 2. State Management (`state.py`)

```python
import sqlite3
import hashlib
from pathlib import Path

class SyncState:
    """SQLite state table for tracking synced screenshots."""

    def __init__(self, db_path="state.db"):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS synced_screenshots (
                    device_path TEXT NOT NULL,
                    content_hash TEXT NOT NULL,
                    synced_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                    book_title TEXT,
                    sync_date TEXT,
                    ocr_text TEXT,
                    PRIMARY KEY (device_path, content_hash)
                )
            """)

    def is_path_synced(self, device_path: str) -> bool:
        """Quick path-only check to skip download for files we've already archived."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "SELECT 1 FROM synced_screenshots WHERE device_path = ?",
                (device_path,)
            )
            return cur.fetchone() is not None

    def is_synced(self, device_path, content_hash):
        """Check if a file has already been synced."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "SELECT 1 FROM synced_screenshots WHERE device_path = ? AND content_hash = ?",
                (device_path, content_hash)
            )
            return cur.fetchone() is not None

    def mark_synced(self, device_path, content_hash, book_title=None, sync_date=None):
        """Mark a file as synced."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO synced_screenshots (device_path, content_hash, book_title, sync_date) VALUES (?, ?, ?, ?)",
                (device_path, content_hash, book_title, sync_date)
            )
```

### 3. Vault Writer (`vault_writer.py`)

```python
from pathlib import Path
from datetime import datetime


def _sanitize(name: str) -> str:
    """Remove characters unsafe for filenames."""
    return "".join(c for c in name if c.isalnum() or c in " .-_").strip()


class VaultWriter:
    """Writes screenshots and reading diary entries to the Obsidian vault."""

    def __init__(self, vault_path):
        self.vault_path = Path(vault_path)

    def write_screenshot(self, book_title, day, png_data, index):
        """
        Write a PNG screenshot to Books/<Title>/<date>-NN.png.

        Returns: embed path used in the book note (e.g. 'Pastoral/2026-07-04-01.png')
        """
        date_str = day.strftime("%Y-%m-%d")
        filename = f"{date_str}-{index:02d}.png"

        book_slug = _sanitize(book_title)
        book_dir = self.vault_path / "Books" / book_slug
        book_dir.mkdir(parents=True, exist_ok=True)
        (book_dir / filename).write_bytes(png_data)

        return f"{book_slug}/{filename}"

    def append_to_daily_note(self, book_title, day, embed_path, ocr_text=None):
        """
        Append a screenshot embed to Books/<Title>.md under a ## YYYY-MM-DD heading.
        """
        date_str = day.strftime("%Y-%m-%d")
        book_slug = _sanitize(book_title)
        note_path = self.vault_path / "Books" / f"{book_slug}.md"

        if not note_path.exists():
            note_path.parent.mkdir(parents=True, exist_ok=True)
            note_path.write_text(f'---\ntitle: "{book_title}"\n---\n')

        with note_path.open("a") as f:
            f.write(f"\n## {date_str}\n\n![[{embed_path}]]\n")

    def write_reading_log(self, day, title, percentage, page=None,
                          total_pages=None, progress=None,
                          prev_percentage=None, prev_day=None):
        """Write today's entry to Reading Log/<date>.md and the all-time
        Reading Log.md — one line per book per day, replaced in place."""
        date_str = day.strftime("%Y-%m-%d")
        log_path = self.vault_path / "Reading Log" / f"{date_str}.md"
        if not log_path.exists():
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(f"# Reading Log — {date_str}\n\n")

        body = (f"{prev_percentage:.1f}% → {percentage:.1f}%"
                if prev_percentage is not None else f"{percentage:.1f}%")
        entry = f"- **{title}** — {body}\n"
        # ...upsert `entry` into both the daily and the all-time logs

    def update_book_timeline(self, title, author, day, percentage, page=None,
                             total_pages=None, progress=None, first_today_pct=None):
        """Append/replace a progress line in Books/<Title>.md under ## <date>."""
        date_str = day.strftime("%Y-%m-%d")
        book_path = self.vault_path / "Books" / f"{_sanitize(title)}.md"
        if not book_path.exists():
            book_path.parent.mkdir(parents=True, exist_ok=True)
            book_path.write_text(
                f'---\ntitle: "{title}"\nauthor: "{author or "Unknown"}"\n'
                f'status: "Reading"\nfirst_opened: {date_str}\n---\n'
            )
        # ...append `- <pct>%` (or `- <from>% → <to>%`) under the ## <date>
        #    heading, replacing today's line if present. See vault_writer.py.
```

### 4. Screenshot Archiver (`archiver.py`)

```python
import asyncio
import hashlib
import io
import aiohttp
from PIL import Image
from datetime import datetime
from .status_display import x4_status

class ScreenshotArchiver:
    """Polls the X4, downloads screenshots, writes to vault."""

    def __init__(self, vault_path, device_host="crosspoint.local", state_db="state.db"):
        self.vault_path = vault_path
        self.device_host = device_host
        self.state = SyncState(state_db)
        self.vault = VaultWriter(vault_path)

    async def run_sync(self):
        """Main sync loop — called when device is detected."""
        async with aiohttp.ClientSession() as session:
            async with x4_status(self.device_host) as show:
                await show("Syncing screenshots...")

                screenshots = await self._list_screenshots(session)
                if not screenshots:
                    await show("No new screenshots")
                    await asyncio.sleep(2)
                    return

                total = len(screenshots)
                newly_archived = 0
                for idx, (book, day, filepath) in enumerate(screenshots, 1):
                    await show(f"Screenshot {idx}/{total} — {book[:20]}")

                    if self.state.is_path_synced(filepath):
                        continue

                    content = await self._download_file(session, filepath)
                    content_hash = hashlib.sha256(content).hexdigest()

                    if self.state.is_synced(filepath, content_hash):
                        continue

                    png_data = self._bmp_to_png(content)
                    embed_path = self.vault.write_screenshot(book, day, png_data, idx)
                    self.vault.append_to_daily_note(book, day, embed_path)
                    self.state.mark_synced(filepath, content_hash, book, day.isoformat())
                    newly_archived += 1

                await show(f"✅ Archived {newly_archived} new screenshot{'s' if newly_archived != 1 else ''}")

    async def _list_screenshots(self, session: aiohttp.ClientSession):
        """Return (book, day, filepath) tuples without downloading — dedup before download."""
        base = f"http://{self.device_host}/api/files"
        async with session.get(base, params={"path": "/screenshots"}) as resp:
            items = await resp.json()

        screenshots = []
        for item in items:
            if not item["isDirectory"]:
                continue
            book = item["name"]
            async with session.get(base, params={"path": f"/screenshots/{book}"}) as resp:
                files = await resp.json()

            for f in files:
                if f["isDirectory"] or not f["name"].endswith(".bmp"):
                    continue
                filepath = f"/screenshots/{book}/{f['name']}"
                day = datetime.fromtimestamp(f.get("mtime", datetime.now().timestamp())).date()
                screenshots.append((book, day, filepath))

        return screenshots

    async def _download_file(self, session: aiohttp.ClientSession, path: str) -> bytes:
        async with session.get(
            f"http://{self.device_host}/download", params={"path": path}
        ) as resp:
            return await resp.read()

    @staticmethod
    def _bmp_to_png(bmp_data: bytes) -> bytes:
        """Convert BMP bytes to PNG bytes using Pillow."""
        img = Image.open(io.BytesIO(bmp_data))
        output = io.BytesIO()
        img.save(output, format="PNG")
        return output.getvalue()
```

### 5. KOReader Sync Server (`koreader_sync.py`)

KOReader's kosync protocol sends an opaque `document` hash (`md5(filename)`),
a `progress` position string and a `percentage` — no page numbers, no title.

```python
class ProgressIn(BaseModel):
    document:   str
    progress:   str = "0"
    percentage: float = 0.0
    device:     str = ""
    device_id:  str = ""
    title:  Optional[str] = None   # CrossPoint leaves these empty
    author: Optional[str] = None

@app.post("/syncs/progress")
@app.put("/syncs/progress")            # some KOReader builds use PUT
async def put_progress(update: ProgressIn, _: Auth):
    record = _store.upsert(update.document, update.progress, update.percentage, ...)
    await _write_progress_to_vault(update)   # resolves title via the alias table
    return record

@app.get("/syncs/progress/{document:path}")
async def get_progress(document: str, _: Auth):
    return _store._latest(document) or {}
```

The `progress_updates` columns are `document, progress (TEXT), percentage
(REAL), device, device_id, title, author, timestamp`. Auth is optional HTTP
Basic (`SYNC_USER` / `SYNC_PASSWORD`); `/users/create` and `/users/auth` are
stubbed because KOReader calls them. See `koreader_sync.py` for the full
implementation (title resolution + ntfy notifications).

### 6. Main Service (`main.py`)

```python
import asyncio
import os
import uvicorn
from xteink_service.archiver import ScreenshotArchiver
from xteink_service.koreader_sync import app as koreader_app
from xteink_service.watcher import poll_for_device, wait_for_offline


async def watcher_loop(host, vault, state_db):
    while True:
        await poll_for_device(host)
        await ScreenshotArchiver(vault, host, state_db).run_sync()
        # resolve any new KOReader hashes while the device is still reachable
        await wait_for_offline(host)


async def main():
    host     = os.getenv("DEVICE_HOST", "crosspoint.local")
    vault    = os.getenv("VAULT_PATH",  "/data/vault")
    state_db = os.getenv("STATE_DB",    "/data/state/state.db")
    port     = int(os.getenv("PORT", "8090"))

    config = uvicorn.Config(koreader_app, host="0.0.0.0", port=port, log_level="info")
    # Run the KOReader sync server + device watcher loop concurrently.
    await asyncio.gather(
        uvicorn.Server(config).serve(),
        watcher_loop(host, vault, state_db),
    )
```

Run it with `python -m xteink_service` (see `__main__.py`).

## Configuration

All config via environment variables (set in `docker-compose.yml`):

| Variable | Default | Description |
|----------|---------|-------------|
| `DEVICE_HOST` | `crosspoint.local` | X4 hostname or IP |
| `VAULT_PATH` | `/data/vault` | Path to Obsidian vault |
| `STATE_DB` | `/data/state/state.db` | SQLite state file |
| `NTFY_TOPIC` | _(unset)_ | ntfy.sh topic for notifications |
| `HA_WEBHOOK` | _(unset)_ | Home Assistant webhook URL |
| `POLL_INTERVAL` | `5` | Device poll interval in seconds |

## API Endpoints (X4)

| Method | Path | Purpose |
|--------|------|---------|
| `GET` | `/api/status` | Device status, uptime, free heap |
| `GET` | `/api/files?path=/screenshots` | List screenshots by book folder |
| `GET` | `/download?path=...` | Download a file |
| `POST` | `/delete` | Delete a file (optional) |
| `WS` | `ws://crosspoint.local:81/` | Status display (START/READY/DONE) |

## KOReader Sync Protocol (kosync)

**POST / PUT `/syncs/progress`** — send a progress update

Request body:
```json
{
  "document": "d41d8cd98f00b204e9800998ecf8427e",
  "progress": "/body/DocFragment[8]/body/p[15]/text().234",
  "percentage": 0.45,
  "device": "Xteink X4",
  "device_id": "x4-xxxxx"
}
```

`document` is `md5(filename)`; CrossPoint sends no title/author (resolved via
the alias table). The server echoes back the stored record.

**GET `/syncs/progress/{document}`** — last known position

Response:
```json
{
  "id": 1,
  "document": "d41d8cd9...",
  "progress": "/body/DocFragment[8]/...",
  "percentage": 0.45,
  "timestamp": 1751500800
}
```

## WebSocket Status Protocol (Port 81)

```
Client → START:message:size:path
Server → READY
Client → [binary data] (optional)
Server → PROGRESS:received:total
Server → DONE / ERROR
```

**Screen behavior:** Displays "Uploading: message" with optional progress bar.

## Error Handling

- WebSocket failure: log warning, continue sync
- Download failure: retry on next poll
- Vault write failure: retry, mark not synced
- Sync server failure: log error, continue watching