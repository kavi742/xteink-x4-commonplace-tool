# Implementation Guide

## Project Structure

```
xteink-x4-commonplace-tool/
├── xteink_service/
│   ├── __init__.py
│   ├── main.py                 # Entry point, orchestrates all services
│   ├── watcher.py              # Device poll loop
│   ├── archiver.py             # Screenshot download, conversion, vault write
│   ├── status_display.py       # WebSocket connection to X4 port 81
│   ├── koreader_sync.py        # KOReader sync server (FastAPI)
│   ├── vault_writer.py         # Obsidian vault file operations
│   ├── state.py                # SQLite state management
│   └── notifications.py        # ntfy.sh / Home Assistant
├── config/                     # (config via environment variables, see docker-compose.yml)
├── tests/
├── requirements.txt
├── README.md
├── ARCHITECTURE.md
├── PROJECT_SPEC.md
└── TODO.md
```

## Dependencies

```txt
# requirements.txt
aiohttp>=3.9.0
aiosqlite>=0.19.0
websockets>=12.0
Pillow>=10.0.0
fastapi>=0.104.0
uvicorn>=0.24.0
pydantic>=2.5.0
```

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

    def write_screenshot(self, book_title, date, png_data, index):
        """
        Write a PNG screenshot to the vault.

        Returns: relative path for Markdown embed
        """
        date_str = date.strftime("%Y-%m-%d")
        filename = f"{date_str}-{index:02d}.png"

        # Create directories
        book_dir = self.vault_path / "Commonplace" / _sanitize(book_title)
        attachments_dir = book_dir / "attachments"
        attachments_dir.mkdir(parents=True, exist_ok=True)

        # Write PNG file
        filepath = attachments_dir / filename
        with open(filepath, "wb") as f:
            f.write(png_data)

        return f"attachments/{filename}"

    def append_to_daily_note(self, book_title, date, embed_path):
        """
        Append a screenshot embed to the daily note for this book.
        """
        date_str = date.strftime("%Y-%m-%d")
        book_dir = self.vault_path / "Commonplace" / _sanitize(book_title)
        note_path = book_dir / f"{date_str}.md"

        # Create note if it doesn't exist
        if not note_path.exists():
            note_path.parent.mkdir(parents=True, exist_ok=True)
            note_path.write_text(f"# {date_str} — {book_title}\n\n")

        # Append the embed
        with open(note_path, "a") as f:
            f.write(f"![[{embed_path}]]\n")

    def write_reading_log(self, date, title, page, total_pages):
        """Append a reading log entry to the daily diary."""
        date_str = date.strftime("%Y-%m-%d")
        log_path = self.vault_path / "Reading Log" / f"{date_str}.md"

        # Create note if it doesn't exist
        if not log_path.exists():
            log_path.parent.mkdir(parents=True, exist_ok=True)
            log_path.write_text(f"# Reading Log — {date_str}\n\n")

        percent = int((page / total_pages) * 100) if total_pages else 0
        entry = f"- **{title}** → Page {page}/{total_pages} ({percent}%)\n"

        with open(log_path, "a") as f:
            f.write(entry)

    def update_book_timeline(self, title, author, date, page, total_pages):
        """Update the per‑book timeline with frontmatter."""
        date_str = date.strftime("%Y-%m-%d")
        book_path = self.vault_path / "Books" / f"{_sanitize(title)}.md"

        percent = int((page / total_pages) * 100) if total_pages else 0
        entry = f"- **{date_str}**: {percent}% (Page {page}/{total_pages})\n"

        if book_path.exists():
            # Append to existing timeline
            with open(book_path, "a") as f:
                f.write(entry)
        else:
            # Create new book note with frontmatter
            frontmatter = f"""---
title: "{title}"
author: "{author or 'Unknown'}"
status: "Reading"
first_opened: {date_str}
last_sync: {date.isoformat()}
---

## Reading Timeline
{entry}
"""
            book_path.parent.mkdir(parents=True, exist_ok=True)
            book_path.write_text(frontmatter)
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

```python
import os
import sqlite3
from datetime import datetime
from fastapi import FastAPI
from pydantic import BaseModel

app = FastAPI()

class ProgressUpdate(BaseModel):
    doc_id: str
    page: int
    total_pages: int = None
    progress: float = None
    device_id: str = None
    metadata: dict = None

class ProgressStore:
    def __init__(self, db_path: str):
        self.db_path = db_path
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS progress_updates (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    doc_id TEXT NOT NULL,
                    page INTEGER,
                    total_pages INTEGER,
                    progress REAL,
                    device_id TEXT,
                    title TEXT,
                    author TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            """)

    def store(self, update: ProgressUpdate):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO progress_updates
                   (doc_id, page, total_pages, progress, device_id, title, author)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    update.doc_id, update.page, update.total_pages, update.progress,
                    update.device_id,
                    update.metadata.get("title") if update.metadata else None,
                    update.metadata.get("author") if update.metadata else None,
                )
            )

    def query(self, doc_id: str = None) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            if doc_id:
                cur = conn.execute(
                    "SELECT * FROM progress_updates WHERE doc_id = ? ORDER BY timestamp DESC",
                    (doc_id,)
                )
            else:
                cur = conn.execute(
                    "SELECT * FROM progress_updates ORDER BY timestamp DESC LIMIT 100"
                )
            return [dict(row) for row in cur.fetchall()]

_store = ProgressStore(os.getenv("STATE_DB", "/data/state/koreader.db"))
_vault = VaultWriter(os.getenv("VAULT_PATH", "/data/vault"))

@app.post("/syncs/progress")
async def sync_progress(update: ProgressUpdate):
    """Receive progress update from KOReader."""
    _store.store(update)

    title = update.metadata.get("title") if update.metadata else update.doc_id
    author = update.metadata.get("author") if update.metadata else "Unknown"
    date = datetime.now()

    _vault.write_reading_log(date, title, update.page, update.total_pages or 0)
    _vault.update_book_timeline(title, author, date, update.page, update.total_pages or 0)

    return {"status": "ok"}

@app.get("/syncs/progress")
async def get_progress(doc_id: str = None):
    """Query progress history."""
    return _store.query(doc_id)
```

### 6. Main Service (`main.py`)

```python
import asyncio
import os
import uvicorn
from xteink_service.watcher import poll_for_device, wait_for_offline
from xteink_service.archiver import ScreenshotArchiver
from xteink_service.koreader_sync import app

async def main():
    host = os.getenv("DEVICE_HOST", "crosspoint.local")
    vault = os.getenv("VAULT_PATH", "/data/vault")
    state_db = os.getenv("STATE_DB", "/data/state/state.db")
    poll_interval = int(os.getenv("POLL_INTERVAL", "5"))

    # Start KOReader sync server in background
    config = uvicorn.Config(app, host="0.0.0.0", port=8081, log_level="info")
    asyncio.create_task(uvicorn.Server(config).serve())

    # Poll for device, sync, then wait for it to go offline before repeating
    archiver = ScreenshotArchiver(vault, host, state_db)
    while True:
        await poll_for_device(host, interval=poll_interval)
        await archiver.run_sync()
        await wait_for_offline(host, interval=poll_interval)

if __name__ == "__main__":
    asyncio.run(main())
```

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

## KOReader Sync Protocol

**POST `/syncs/progress`** — Send progress update

Request body:
```json
{
  "doc_id": "hash_of_filename",
  "page": 145,
  "total_pages": 320,
  "progress": 0.45,
  "device_id": "x4-xxxxx",
  "metadata": {
    "title": "The Great Gatsby",
    "author": "F. Scott Fitzgerald"
  }
}
```

Response:
```json
{"status": "ok"}
```

**GET `/syncs/progress?doc_id=<id>`** — Query history

Response:
```json
[
  {"id": 1, "doc_id": "...", "page": 145, "title": "The Great Gatsby", "timestamp": "2026-07-02T14:32:00Z"},
  ...
]
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