# Project Specification

## Overview

A homelab service that automatically archives screenshots and reading progress from the Xteink X4 e‑ink reader into an Obsidian vault, with real‑time on‑device feedback.

## User Stories

1. **As a reader**, when I press File Transfer on my X4, I want my screenshots to appear in my Obsidian vault automatically, organized by book and by day.

2. **As a reader**, I want to see progress and status messages on the X4 screen while screenshots are syncing, so I know what's happening.

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
- The service MUST write PNGs to `Commonplace/<Book>/attachments/`.
- The service MUST write/append to `Commonplace/<Book>/YYYY-MM-DD.md`.
- The service MUST mark synced files in the state table.

### FR3: On‑Device Status Display
- The service MUST connect to WebSocket port 81 on the X4.
- The service MUST send START messages to display status text.
- The service MUST show progress updates during sync.
- The service MUST show completion or error messages.
- The service MUST gracefully continue if the WebSocket connection fails.

### FR4: KOReader Sync Integration
- The service MUST run a KOReader sync server (POST /syncs/progress, GET /syncs/progress).
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
- WebSocket failures MUST NOT block screenshot sync.
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
3. The user has enabled "Send Document Metadata" in X4's KOReader Sync settings.
4. The X4's File Transfer mode is used regularly enough that the 5‑10s poll catches it.
5. The `tesseract-ocr` system binary is available in the deployment
   environment (bundled in the Docker image); OCR accuracy on e‑ink
   screenshots is best‑effort and depends on font rendering and whether
   the page contains illustrations, not a guaranteed transcription.

## User Interface

### On‑Device (X4 Screen)
- "Uploading: <status message>" — displayed automatically when WebSocket START is sent.
- Progress bar updates with PROGRESS messages.
- "Completed" briefly after DONE.

### Status Page (FastAPI)
- URL: `http://homelab.local:8090/status`
- Shows: last sync time, books touched today, total screenshots, recent errors.

### Notifications
- ntfy.sh or Home Assistant push notifications.

## Data Model

### synced_screenshots (SQLite)
| Column | Type | Description |
|--------|------|-------------|
| device_path | TEXT | Path on the X4 SD card |
| content_hash | TEXT | SHA‑256 hash of file content |
| synced_at | TIMESTAMP | When it was synced |
| book_title | TEXT | Book folder name |
| sync_date | TEXT | Calendar day (YYYY-MM-DD) |
| ocr_text | TEXT | Extracted text from Tesseract (nullable — empty if OCR skipped/failed) |
| PRIMARY KEY | (device_path, content_hash) | |

### progress_updates (SQLite)
| Column | Type | Description |
|--------|------|-------------|
| id | INTEGER | Primary key |
| doc_id | TEXT | KOReader document ID |
| page | INTEGER | Current page |
| total_pages | INTEGER | Total pages |
| progress | REAL | Progress (0.0‑1.0) |
| device_id | TEXT | Device identifier |
| title | TEXT | Book title (from metadata) |
| author | TEXT | Book author (from metadata) |
| timestamp | TIMESTAMP | When update was received |

## Vault Structure

```
vault/
  Commonplace/
    <Book Title>/
      YYYY-MM-DD.md          # Daily note: screenshot embeds + OCR text callouts
      attachments/
        YYYY-MM-DD-01.png    # Screenshots
        YYYY-MM-DD-02.png
  Reading Log/
    YYYY-MM-DD.md            # Daily diary with reading progress
  Books/
    <Book Title>.md          # Per‑book timeline with frontmatter
```

## Build Order

1. Vault folder structure + Syncthing setup
2. Device watcher (poll loop, log detection)
3. WebSocket status display (manual test)
4. Screenshot archiver (list, download, convert, write)
5. OCR integration (pytesseract, embedded in daily notes, graceful fallback)
6. State persistence (SQLite)
7. KOReader sync server
8. Vault writer integration (reading log, book timelines)
9. Notifications
10. Status page (FastAPI)