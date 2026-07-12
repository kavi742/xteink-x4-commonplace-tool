# Project Specification

## Overview

A homelab service that automatically archives screenshots and reading progress from the Xteink X4 e‑ink reader into an Obsidian vault, with server‑side status logging and estimated page numbers.

## User Stories

1. **As a reader**, when I press File Transfer on my X4, I want my screenshots to appear in my Obsidian vault automatically, organized by book and by day.

2. **As a reader**, I want the service to look up each book's page count automatically, so my reading progress is shown as an estimated page number (e.g. `p149 / ~175`), not just a percentage.

3. **As a reader**, I want my reading progress (page number, book title) to be automatically logged to a daily reading diary in Obsidian.

4. **As a reader**, I want to see a timeline of my reading for each book, showing when I read and how far I progressed.

5. **As a reader**, I want this to work with the Crosspoint Firmware — no custom code on the device.

6. **As a reader**, I want to be able to access my vault from any device (laptop, phone, tablet) via Syncthing or similar.

## Functional Requirements

### FR1: Device Detection
- The service MUST poll `http://crosspoint.local/api/status` every 5‑10 seconds.
- The service MUST detect when the X4 enters File Transfer / Calibre Wireless mode.
- The service MUST fall back to the device's last‑known IP if mDNS resolution fails.

### FR2: Screenshot Archiving
- The service MUST list all files under `/screenshots/` via `/api/files`.
- The service MUST group screenshots by (book folder, calendar day of mtime).
- The service MUST skip files already in the sync state table.
- The service MUST download BMP files via `/download`.
- The service MUST convert BMP to PNG using Pillow.
- The service MUST OCR each PNG using `pytesseract`/Tesseract and embed the
  extracted text in the daily note (e.g. under a collapsible callout below
  the image embed) so screenshots are full‑text searchable within
  Obsidian's native search — Obsidian does not OCR images itself.
- The service MUST gracefully skip OCR on a given image (logging a warning,
  not failing the sync) if Tesseract is unavailable or extraction errors
  out, per the project's graceful‑degradation principle.
- The service MUST store the extracted OCR text in the state table so a
  re‑run never re‑OCRs an already‑synced file.
- The service MUST write PNGs to `Books/<Title>/<date>-NN.png`.
- The service MUST write/append screenshot embeds to `Books/<Title>.md` under a `## YYYY-MM-DD` date heading.
- The service MUST mark synced files in the state table.

### FR3: Sync Status (server-side)
- The service MUST log sync progress server-side (visible via `docker compose logs`).
- The service MUST NOT send status text to the device: port 81's `START:name:size:path` protocol is a Calibre-Wireless *file-upload* channel, and using it for status left 0-byte junk files at the device root.
- The service MUST remove leftover 0-byte junk files from the device root during File Transfer (`cleanup_device_junk`).

### FR3b: Page-Count Lookup
- During File Transfer, the service MUST look up a total page count for each book that has reading progress — Open Library first, an epub word-count estimate as fallback.
- The service MUST cache page counts (the `book_pages` table) so each book is looked up only once.
- The service MUST expose page numbers derived from reading percentage (`percentage × total_pages`) in the reading log and per-book stats.
- Page lookup failures MUST be non-fatal (a book simply has no page estimate).

### FR4: KOReader Sync Integration
- The service MUST run a KOReader (kosync) server: `POST`/`PUT /syncs/progress`, `GET /syncs/progress/{document}`, plus the `/users/create` + `/users/auth` auth stubs KOReader expects.
- The service MUST store incoming progress updates in a SQLite database.
- The service MUST write progress to `Reading Log/YYYY-MM-DD.md`.
- The service MUST update per‑book timeline in `Books/<Title>.md`.
- The service MUST use metadata (title, author) if available.

### FR5: Observability
- The service MUST send notifications (ntfy.sh or Home Assistant) for:
  - Successful screenshot sync
  - KOReader progress update
  - Sync failures
- The service MUST provide a read‑only FastAPI status page showing:
  - Last sync time
  - Books touched today
  - Total screenshots archived
  - Recent KOReader updates
  - Last error (if any)

### FR6: State Persistence
- The service MUST maintain a SQLite table of synced screenshots keyed by (device_path, content_hash).
- The service MUST maintain a SQLite table of KOReader progress updates.

## Non‑Functional Requirements

### NFR1: Performance
- Poll interval: 5‑10 seconds.
- Screenshot download: < 10 seconds for 5 screenshots.
- OCR: < 2 seconds per screenshot (Tesseract, CPU‑only).
- Vault write: < 500ms per file.

### NFR2: Reliability
- Failed downloads MUST retry on the next poll cycle.
- Page-count lookup or status logging failures MUST NOT block screenshot sync.
- State MUST be persisted to disk and survive restarts.

### NFR3: Maintainability
- Code MUST be modular with clear separation of concerns.
- Configuration MUST be externalized (YAML or environment variables).
- Logging MUST be sufficient for debugging.

### NFR4: Compatibility
- MUST work with Crosspoint Firmware.
- MUST work with Obsidian (any version) via filesystem.
- MUST work with Syncthing (or other sync tools) for cross‑device vault access.

## Constraints

1. **Crosspoint Firmware**: The X4 runs CrossPoint firmware.
2. **No Obsidian API**: Only filesystem writes; no Obsidian plugin required.
3. **No cloud dependencies**: All services run on the homelab.
4. **No authentication on X4**: The device's web server is unauthenticated by design.

## Assumptions

1. The X4 is on the same LAN as the homelab server (or reachable via Tailscale).
2. The user has a Syncthing (or equivalent) sync set up between the homelab vault folder and Obsidian clients.
3. The user relies on automatic title resolution from the device file listing (`alias.py --scan`); CrossPoint firmware has no "Send Document Metadata" option, so KOReader progress arrives with empty title/author.
4. The X4's File Transfer mode is used regularly enough that the 5‑10s poll catches it.
5. The `tesseract-ocr` system binary is available in the deployment
   environment (bundled in the Docker image); OCR accuracy on e‑ink
   screenshots is best‑effort and depends on font rendering and whether
   the page contains illustrations, not a guaranteed transcription.

## User Interface

### On‑Device (X4 Screen)
- None. The device screen is not used for status — its port-81 channel is a file-upload protocol, not a display (see FR3). Sync status is logged server-side instead.

### Status Page (FastAPI)
- URL: `http://homelab.local:8090/status`
- Shows: last sync time, books touched today, total screenshots, recent errors.

### Notifications
- ntfy.sh or Home Assistant push notifications.

## Data Model

### synced_screenshots (SQLite)
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key (autoincrement) |
| device_path | TEXT | Path on the X4 SD card |
| content_hash | TEXT | SHA‑256 hash of file content |
| synced_at | TIMESTAMP | When it was synced |
| book_title | TEXT | Book folder name |
| sync_date | TEXT | Calendar day (YYYY-MM-DD) |
| ocr_text | TEXT | Extracted text from Tesseract (nullable) |
| vault_png_path | TEXT | Relative path to the PNG in the vault |
| ocr_corrected | TEXT | User-edited OCR correction (nullable) |
| user_notes | TEXT | Free-form per-screenshot notes (nullable) |
| _constraint_ | | UNIQUE(device_path, content_hash) |

### progress_updates (SQLite)
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| document | TEXT | KOReader document hash (`md5(filename)`) |
| progress | TEXT | KOReader position (XPath/CFI string) |
| percentage | REAL | Progress (0.0‑1.0) |
| device | TEXT | Device name |
| device_id | TEXT | Device identifier |
| title | TEXT | Book title (usually empty; resolved via alias table) |
| author | TEXT | Book author (usually empty) |
| timestamp | INTEGER | Unix epoch seconds when received |

## Vault Structure

```
vault/
  Books/
    <Book Title>.md          # Screenshots + reading progress, interleaved by date
    <Book Title>/
      YYYY-MM-DD-01.png      # Screenshot images (OCR text in iTXt metadata)
      YYYY-MM-DD-01.json     # JSON sidecar: device path, hash, OCR, timestamp
  Reading Log/
    YYYY-MM-DD.md            # Daily reading diary
  Reading Log.md             # All-time reading log, newest day first
```

## Build Order

1. Vault folder structure + Syncthing setup
2. Device watcher (poll loop, log detection)
3. Sync status logging (server-side)
4. Screenshot archiver (list, download, convert, write)
5. OCR integration (pytesseract, embedded in daily notes, graceful fallback)
6. State persistence (SQLite)
7. KOReader sync server
8. Vault writer integration (reading log, book timelines)
9. Notifications
10. Status page (FastAPI)