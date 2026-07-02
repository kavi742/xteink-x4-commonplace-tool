# Architecture

## Overview

Three core services run continuously on your homelab:

1. **Device Watcher + Screenshot Archiver** — polls for the X4, pulls screenshots, writes to Obsidian vault
2. **KOReader Sync Server** — receives reading progress updates from the X4
3. **Status Display (WebSocket)** — shows progress and errors on the X4 screen

All services are independent but share state via a SQLite database.

## System Diagram

```
┌─────────────────────────────────────────────────────────────────────────────┐
│ Xteink X4 (Crosspoint Firmware)                                           │
│                                                                              │
│  ┌──────────────────┐     ┌──────────────────┐     ┌──────────────────┐    │
│  │ File Transfer    │     │ KOReader Sync    │     │ WebSocket        │    │
│  │ Mode (port 80)   │     │ (settings)       │     │ Server (port 81) │    │
│  └────────┬─────────┘     └────────┬─────────┘     └────────┬─────────┘    │
└───────────┼────────────────────────┼────────────────────────┼───────────────┘
            │ HTTP API               │ HTTP POST              │ WebSocket
            │ /api/files, /download  │ /syncs/progress        │ START messages
            ▼                        ▼                        ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Homelab Service (Python)                                                    │
│                                                                              │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │ Device Watcher (poll every 5‑10s)                                    │    │
│  │  • GET /api/status → device online?                                 │    │
│  │  • Connect WebSocket port 81 → on‑device status                    │    │
│  │  • GET /api/files?path=/screenshots → list screenshots             │    │
│  │  • Download via /download?path=...                                 │    │
│  │  • Convert BMP→PNG (Pillow)                                       │    │
│  │  • Write to Obsidian vault                                         │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                           │                                                  │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │ KOReader Sync Server                                                 │    │
│  │  • POST /syncs/progress → receives progress updates                │    │
│  │  • GET /syncs/progress → query history                             │    │
│  │  • Stores updates in SQLite                                       │    │
│  │  • Triggers vault write callback                                  │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
│                           │                                                  │
│  ┌─────────────────────────────────────────────────────────────────────┐    │
│  │ State & Observability                                               │    │
│  │  • SQLite state table (synced screenshots)                         │    │
│  │  • ntfy.sh / Home Assistant notifications                          │    │
│  │  • FastAPI status page (read‑only)                                │    │
│  └─────────────────────────────────────────────────────────────────────┘    │
└─────────────────────────────────────────────────────────────────────────────┘
            │
            ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Obsidian Vault (filesystem)                                                │
│                                                                              │
│  vault/                                                                     │
│    Commonplace/<Book Title>/                                                │
│      YYYY-MM-DD.md  ← daily note with screenshot embeds                    │
│      attachments/    ← PNG files                                           │
│    Reading Log/                                                             │
│      YYYY-MM-DD.md   ← daily reading diary                                 │
│    Books/                                                                   │
│      <Book Title>.md  ← per‑book timeline                                  │
└─────────────────────────────────────────────────────────────────────────────┘
            │
            │  **Note:** The service creates Obsidian-formatted Markdown files.
            │  Obsidian itself does not run on the server — the vault is synced
            │  via Syncthing to your laptop/phone where you view the notes.
            ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│ Syncthing                                                                   │
│  • Syncs vault folder to Obsidian clients                                   │
│  • Works across laptop, phone, tablet                                      │
└─────────────────────────────────────────────────────────────────────────────┘
```

## Component Descriptions

### 1. Device Watcher & Screenshot Archiver

**Approach: mDNS Service Discovery (Event-Driven)**

Instead of polling for the device, we use mDNS/Bonjour to detect when the X4 enters File Transfer mode. This is event-driven and more efficient than polling.

**How it works:**
1. The CrossPoint firmware advertises an HTTP service via mDNS when in File Transfer mode
2. We subscribe to mDNS service discovery events
3. When the device appears, we get an immediate notification
4. This triggers the screenshot sync workflow

**Why mDNS instead of polling:**
- No constant network traffic
- Immediate detection when device comes online
- More reliable across different network configurations
- Industry-standard (used by Apple's ecosystem)

**Implementation:** Use `python-zeroconf` library to listen for `_http._tcp.local.` services. When `crosspoint.local` is discovered, resolve its IP and connect.

**Fallback:** If mDNS fails, fall back to polling `http://crosspoint.local/api/status` every 5‑10 seconds.

**On detection:**
1. Connect WebSocket to `ws://crosspoint.local:81/`
2. Send `START:Syncing screenshots...:1:/` — appears on X4 screen
3. List `/screenshots/` via `/api/files?path=/screenshots`
4. Group new files by (book, calendar day)
5. For each file, show progress: `START:Screenshot 3 of 5:1:/`
6. Download via `/download?path=...`
7. Convert BMP → PNG using Pillow
8. Write PNGs to `Commonplace/<Book>/attachments/`
9. Write/append to `Commonplace/<Book>/YYYY-MM-DD.md`
10. Mark synced in SQLite state table
11. Show completion: `START:✅ Archived 5 screenshots from "Title":1:/`
12. Send `DONE` and close WebSocket

**State:** SQLite table `synced_screenshots` keyed by `(device_path, content_hash)`.

**Fallback:** If WebSocket fails, continue without on‑device feedback.

### 2. KOReader Sync Server

**Protocol:** Implements KOReader sync API:
- `POST /syncs/progress` — receives progress update
- `GET /syncs/progress?doc_id=<id>` — query history

**Data received:**
```json
{
  "doc_id": "hash_of_filename",
  "page": 145,
  "total_pages": 320,
  "metadata": {
    "title": "The Great Gatsby",
    "author": "F. Scott Fitzgerald"
  }
}
```

**Processing:**
1. Store update in SQLite with timestamp
2. Resolve book identity (use `title` from metadata if available)
3. Write to `Reading Log/YYYY-MM-DD.md`: `- **Title** → Page X/Y (Z%)`
4. Write to `Books/<Title>.md`: append timeline entry with frontmatter

**Server options:**
- Korrosync (Rust, Docker‑ready)
- koreader-sync (Python, easy to modify)
- Custom minimal implementation (integrated with main service)

### 3. On‑Device Status Display

**Protocol:** WebSocket to port 81 (same as Calibre plugin uses).

**Message flow:**
```
Client → START:message:size:path
Server → READY
Client → [binary data] (dummy byte optional)
Server → PROGRESS:received:total
Server → DONE / ERROR
```

**Display behavior:**
- Shows "Uploading: <message>" on the X4 screen
- Progress bar updates with `PROGRESS` messages
- Message clears ~6 seconds after `DONE`

**Usage:**
```python
ws = await websockets.connect("ws://crosspoint.local:81/")
await ws.send("START:Screenshot 3 of 5:1:/")
await ws.recv()  # READY
await ws.send(b"X")  # optional dummy data
# Screen updates automatically
```

### 4. Observability Layer

**Notifications:** ntfy.sh or Home Assistant webhook:
- "📎 Archived N screenshots — Book Title"
- "📖 Progress updated — Book Title → page 145"
- "⚠️ Sync failed (attempt N)"

**Status Page:** FastAPI + Jinja template:
- Last sync time
- Books touched today
- Total screenshots archived
- Recent KOReader sync updates
- Last error (if any)

## Data Flow

```
Screenshot Flow:
X4 → HTTP GET /download → BMP → Pillow → PNG → Vault/Commonplace/...

Reading Progress Flow:
X4 → HTTP POST /syncs/progress → Sync Server → SQLite → Vault/Reading Log/...

Status Display Flow:
Archiver → WebSocket ws://X4:81 → X4 screen shows "Uploading: message"
```

## Network Access Options

| Access Method | Configuration | Best For |
| :--- | :--- | :--- |
| **mDNS Service Discovery** | Event-driven via `python-zeroconf` | Always-on home network, recommended for reliability |
| **Local LAN** | `http://192.168.1.100:8081` | At home, simple setup |
| **Docker (host mode)** | `http://localhost:8081` | Docker with `network_mode: host` |
| **Nginx Proxy Manager** | `https://sync.yourdomain.com` | Secure remote access |
| **Tailscale** | `http://100.x.x.x:8081` | Simple remote access, no public exposure |

**Note for HTTPS:** The X4 may need a community firmware fork (e.g., CrossPoint Reader ++) to handle TLS memory constraints when connecting to HTTPS servers.

## Docker Deployment (Recommended)

```yaml
# docker-compose.yml
services:
  xteink:
    build: .
    network_mode: host  # Required for mDNS/crosspoint.local
    volumes:
      - ./vault:/data/vault
      - ./config:/data/config
      - ./state:/data/state
    environment:
      - DEVICE_HOST=crosspoint.local
    restart: unless-stopped
```

**Key points:**
- Use `network_mode: host` for mDNS resolution (`crosspoint.local`)
- Mount your Obsidian vault to `/data/vault`
- State directory persists the SQLite database across restarts
- Health check endpoint available at `http://localhost:8081/health`

## Reliability Design

- **Additive sync**: source files remain on device unless explicitly deleted
- **Idempotent**: state table prevents duplicate processing
- **Atomic writes**: batch files per (book, day) before Syncthing picks them up
- **Retry**: failed downloads retry on next poll cycle
- **Graceful degradation**: WebSocket failures don't stop screenshot sync
- **Persistence**: SQLite state survives restarts

## Technology Stack

| Component | Technology |
| :--- | :--- |
| **Deployment** | **Docker + docker-compose** (primary) |
| HTTP client | `aiohttp` + `asyncio` |
| WebSocket client | `websockets` |
| Image conversion | `Pillow` |
| Web server (sync) | `FastAPI` + `uvicorn` |
| State storage | `SQLite` (via `aiosqlite`) |
| File watching | None (polling only) |
| Cross‑device sync | Syncthing (external) |
| Notifications | `ntfy.sh` or `Home Assistant` webhook |