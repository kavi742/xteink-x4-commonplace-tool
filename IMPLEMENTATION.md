# Implementation Guide

## Project Structure

```
xteink-service/
├── src/
│   ├── __init__.py
│   ├── main.py                 # Entry point, orchestrates all services
│   ├── watcher.py              # Device poll loop
│   ├── archiver.py             # Screenshot download, conversion, vault write
│   ├── status_display.py       # WebSocket connection to X4 port 81
│   ├── koreader_sync.py        # KOReader sync server (FastAPI)
│   ├── vault_writer.py         # Obsidian vault file operations
│   ├── state.py                # SQLite state management
│   └── notifications.py        # ntfy.sh / Home Assistant
├── config/
│   └── settings.yaml           # Configuration
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
pyyaml>=6.0
python-zeroconf>=0.132.0  # mDNS service discovery
```

## Core Modules

### 0. Device Discovery (`watcher.py`) - mDNS Approach

```python
from zeroconf import ServiceListener, ServiceBrowser, Zeroconf
import asyncio

class XteinkListener(ServiceListener):
    """Event-driven device discovery using mDNS."""

    def __init__(self, on_device_found, on_device_lost):
        self.on_device_found = on_device_found
        self.on_device_lost = on_device_lost

    def add_service(self, zc, type_, name):
        """Called when a service is discovered."""
        if "crosspoint" in name.lower():
            print(f"Device discovered: {name}")
            # Resolve the service to get IP address
            info = zc.get_service_info(type_, name)
            if info:
                host = info.addresses[0] if info.addresses else info.host
                port = info.port
                asyncio.create_task(self.on_device_found(host, port))

    def remove_service(self, zc, type_, name):
        """Called when a service is removed."""
        if "crosspoint" in name.lower():
            print(f"Device lost: {name}")
            asyncio.create_task(self.on_device_lost())


class DeviceWatcher:
    """Watches for X4 device using mDNS service discovery."""

    def __init__(self, on_device_available):
        self.on_device_available = on_device_available
        self.zeroconf = None
        self.browser = None
        self.device_host = None

    async def start(self):
        """Start mDNS service discovery."""
        self.zeroconf = Zeroconf()
        listener = XteinkListener(
            on_device_found=self._handle_device_found,
            on_device_lost=self._handle_device_lost
        )
        self.browser = ServiceBrowser(
            self.zeroconf, 
            "_http._tcp.local.", 
            listener
        )
        print("Watching for X4 via mDNS...")
        # Keep running
        await asyncio.Event().wait()

    async def _handle_device_found(self, host, port):
        """Called when device is discovered."""
        self.device_host = f"{host}:{port}"
        print(f"X4 available at {self.device_host}")
        await self.on_device_available(self.device_host)

    async def _handle_device_lost(self):
        """Called when device goes offline."""
        print("X4 went offline")
        self.device_host = None

    def stop(self):
        """Stop discovery."""
        if self.browser:
            self.browser.cancel()
        if self.zeroconf:
            self.zeroconf.close()
```

**Fallback: Polling-based watcher**

If mDNS is unavailable, fall back to polling:

```python
async def poll_for_device(host="crosspoint.local", interval=5):
    """Poll for device availability."""
    async with aiohttp.ClientSession() as session:
        while True:
            try:
                async with session.get(f"http://{host}/api/status", timeout=3) as resp:
                    if resp.status == 200:
                        return host
            except Exception:
                pass
            await asyncio.sleep(interval)
```

### 1. Status Display (`status_display.py`)

```python
import asyncio
import websockets

class XteinkStatus:
    """Manages WebSocket connection to X4 port 81 for on‑device status messages."""

    def __init__(self, host="crosspoint.local"):
        self.host = host
        self.ws = None
        self.connected = False

    async def connect(self):
        """Establish WebSocket connection to the X4."""
        try:
            self.ws = await websockets.connect(f"ws://{self.host}:81/")
            self.connected = True
            return True
        except Exception as e:
            self.connected = False
            return False

    async def show(self, message, progress=None, total=None):
        """
        Display a status message on the X4 screen.

        Args:
            message: Text shown as "Uploading: <message>"
            progress: Current progress (optional)
            total: Total items (optional)
        """
        if not self.connected or not self.ws:
            return False

        try:
            # Send START message
            await self.ws.send(f"START:{message}:1:/")
            response = await self.ws.recv()
            if response != "READY":
                return False

            # If progress provided, send dummy byte to trigger progress display
            if progress is not None and total is not None:
                await self.ws.send(b"X")
                # Server sends PROGRESS messages automatically
                # We don't need to wait for them

            return True
        except Exception:
            return False

    async def done(self):
        """Send DONE to clear the status message."""
        if not self.connected or not self.ws:
            return

        try:
            await self.ws.send(b"X")
            await self.ws.recv()  # DONE
        except Exception:
            pass

    async def close(self):
        """Close the WebSocket connection."""
        if self.ws:
            await self.ws.close()
            self.connected = False
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
                    PRIMARY KEY (device_path, content_hash)
                )
            """)

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

    def compute_hash(self, data):
        """Compute SHA‑256 hash of file content."""
        return hashlib.sha256(data).hexdigest()
```

### 3. Vault Writer (`vault_writer.py`)

```python
from pathlib import Path
from datetime import datetime

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
        book_dir = self.vault_path / "Commonplace" / self._sanitize(book_title)
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
        book_dir = self.vault_path / "Commonplace" / self._sanitize(book_title)
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
        book_path = self.vault_path / "Books" / f"{self._sanitize(title)}.md"

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

    @staticmethod
    def _sanitize(name):
        """Remove characters unsafe for filenames."""
        return "".join(c for c in name if c.isalnum() or c in " .-_").strip()
```

### 4. Screenshot Archiver (`archiver.py`)

```python
import asyncio
import aiohttp
from PIL import Image
import io
from datetime import datetime

class ScreenshotArchiver:
    """Polls the X4, downloads screenshots, writes to vault."""

    def __init__(self, vault_path, device_host="crosspoint.local"):
        self.vault_path = vault_path
        self.device_host = device_host
        self.state = SyncState()
        self.vault = VaultWriter(vault_path)
        self.status = XteinkStatus(device_host)

    async def run_sync(self):
        """Main sync loop — called when device is detected."""
        await self.status.connect()
        try:
            await self.status.show("Syncing screenshots...")

            # List screenshots
            screenshots = await self._list_screenshots()
            if not screenshots:
                await self.status.show("No new screenshots")
                await asyncio.sleep(2)
                return

            total = len(screenshots)
            for idx, (book, day, filepath, data) in enumerate(screenshots, 1):
                # Show progress on device
                await self.status.show(f"Screenshot {idx}/{total} — {book[:20]}")

                # Download if not already synced
                content_hash = self.state.compute_hash(data)
                if self.state.is_synced(filepath, content_hash):
                    continue

                # Convert BMP to PNG
                png_data = self._bmp_to_png(data)

                # Write to vault
                embed_path = self.vault.write_screenshot(book, day, png_data, idx)
                self.vault.append_to_daily_note(book, day, embed_path)

                # Mark synced
                self.state.mark_synced(filepath, content_hash, book, day.isoformat())

            await self.status.done()
            await self.status.show(f"✅ Archived {total} screenshots")

        except Exception as e:
            await self.status.show(f"❌ Error: {str(e)[:30]}")
            raise
        finally:
            await self.status.close()

    async def _list_screenshots(self):
        """Fetch screenshot list from /api/files and group by (book, day)."""
        async with aiohttp.ClientSession() as session:
            url = f"http://{self.device_host}/api/files?path=/screenshots"
            async with session.get(url) as resp:
                data = await resp.json()

        screenshots = []
        for item in data:
            if item["isDirectory"]:
                book = item["name"]
                # List files inside book folder
                async with aiohttp.ClientSession() as session:
                    url = f"http://{self.device_host}/api/files?path=/screenshots/{book}"
                    async with session.get(url) as resp:
                        files = await resp.json()

                for file in files:
                    if not file["isDirectory"] and file["name"].endswith(".bmp"):
                        # Download file
                        filepath = f"/screenshots/{book}/{file['name']}"
                        data = await self._download_file(filepath)
                        # Use file's mtime or current time as day
                        day = datetime.fromtimestamp(file.get("mtime", datetime.now().timestamp())).date()
                        screenshots.append((book, day, filepath, data))

        return screenshots

    async def _download_file(self, path):
        async with aiohttp.ClientSession() as session:
            url = f"http://{self.device_host}/download?path={path}"
            async with session.get(url) as resp:
                return await resp.read()

    @staticmethod
    def _bmp_to_png(bmp_data):
        """Convert BMP bytes to PNG bytes using Pillow."""
        img = Image.open(io.BytesIO(bmp_data))
        output = io.BytesIO()
        img.save(output, format="PNG")
        return output.getvalue()
```

### 5. KOReader Sync Server (`koreader_sync.py`)

```python
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel
from datetime import datetime
import sqlite3

app = FastAPI()

class ProgressUpdate(BaseModel):
    doc_id: str
    page: int
    total_pages: int = None
    progress: float = None
    device_id: str = None
    metadata: dict = None

class SyncState:
    def __init__(self, db_path="koreader_state.db"):
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

    def store_update(self, update):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """INSERT INTO progress_updates
                   (doc_id, page, total_pages, progress, device_id, title, author)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                (
                    update.doc_id,
                    update.page,
                    update.total_pages,
                    update.progress,
                    update.device_id,
                    update.metadata.get("title") if update.metadata else None,
                    update.metadata.get("author") if update.metadata else None,
                )
            )

state = SyncState()
vault_writer = VaultWriter("/path/to/vault")  # Configure this

@app.post("/syncs/progress")
async def sync_progress(update: ProgressUpdate):
    """Receive progress update from KOReader."""
    state.store_update(update)

    # Extract data
    title = update.metadata.get("title") if update.metadata else update.doc_id
    author = update.metadata.get("author") if update.metadata else "Unknown"
    page = update.page
    total = update.total_pages or 0
    date = datetime.now()

    # Write to Obsidian vault
    vault_writer.write_reading_log(date, title, page, total)
    vault_writer.update_book_timeline(title, author, date, page, total)

    return {"status": "ok"}

@app.get("/syncs/progress")
async def get_progress(doc_id: str = None):
    """Query progress history."""
    with sqlite3.connect(state.db_path) as conn:
        if doc_id:
            cur = conn.execute(
                "SELECT * FROM progress_updates WHERE doc_id = ? ORDER BY timestamp DESC",
                (doc_id,)
            )
        else:
            cur = conn.execute(
                "SELECT * FROM progress_updates ORDER BY timestamp DESC LIMIT 100"
            )
        return cur.fetchall()
```

### 6. Main Service (`main.py`)

```python
import asyncio
import aiohttp
import uvicorn
from src.watcher import DeviceWatcher
from src.koreader_sync import app

async def main():
    # Start KOReader sync server in background
    config = uvicorn.Config(app, host="0.0.0.0", port=8081, log_level="info")
    server = uvicorn.Server(config)
    asyncio.create_task(server.serve())

    # Start device watcher
    watcher = DeviceWatcher()
    await watcher.run()

if __name__ == "__main__":
    asyncio.run(main())
```

## Configuration (`config/settings.yaml`)

```yaml
device:
  host: "crosspoint.local"
  poll_interval: 5  # seconds

vault:
  path: "/path/to/obsidian/vault"

sync_server:
  host: "0.0.0.0"
  port: 8081

notifications:
  ntfy_topic: "xteink"
  home_assistant_webhook: "https://ha.local/webhook/..."

state:
  db_path: "state.db"
```

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