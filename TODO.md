# TODO

## Phase 1: Foundation
- [x] Create Obsidian vault folder structure:
  - [x] `Commonplace/` root
  - [x] `Reading Log/` directory
  - [x] `Books/` directory
  - via `scripts/init-vault.sh` — idempotent, run it whenever
- [ ] Set up Syncthing between homelab and Obsidian clients
  - manual pairing step (Syncthing device IDs), can't be scripted from here
- [x] Configure Syncthing to ignore temporary files
  - `.stignore` written into the vault by `scripts/init-vault.sh`
- [x] **Create Docker setup (Dockerfile, docker-compose.yml)** ← PRIMARY DEPLOYMENT METHOD
  - untested against an actual Docker daemon — verify with `docker build .`

## Phase 2: Device Discovery
- [x] Implement `poll_for_device()` — poll `http://crosspoint.local/api/status` every 5s
- [x] Log device detection events
- [x] Test with actual X4 in File Transfer mode
  - unit tests: 3/3 pass (`tests/test_watcher.py`)
  - live test when device is available: `python xteink_service/watcher.py [host]`

## Phase 3: On‑Device Status Display
- [x] Manually connect to `ws://crosspoint.local:81/`
- [x] Send `START:Test message:1:/` and verify it appears on X4 screen
- [x] Implement `x4_status()` async context manager in Python
- [x] Test `show()` callable and graceful degradation
  - unit tests: 3/3 pass (`tests/test_status_display.py`)
  - live test: `bash test_scripts/test-phase3.sh`

## Phase 4: Screenshot Archiving
- [x] Implement `_list_screenshots()` using `/api/files`
- [x] Test grouping by book folder and day (mtime)
- [x] Implement `_download_file()` using `/download`
- [x] Implement `_bmp_to_png()` conversion with Pillow
- [ ] Add `pytesseract` (Python) + `tesseract-ocr` (system package) dependency
- [ ] Implement `_ocr_image()` to extract text from each PNG via `pytesseract`
- [ ] Embed OCR text under each screenshot embed as a collapsible callout
      (`> [!quote]- OCR text`) so it's indexed by Obsidian search
- [ ] Handle OCR failures gracefully — missing binary, blank/corrupt image —
      log a warning and continue writing the image without text
- [ ] Add `ocr_text` column to the `synced_screenshots` SQLite table
- [ ] Test OCR accuracy against a handful of real X4 screenshots (mixed
      fonts, illustrations) to gauge how reliable it actually is
- [ ] Implement `VaultWriter.write_screenshot()` and `append_to_daily_note()`
- [ ] Test end‑to‑end with 3‑5 screenshots

## Phase 5: State Management & Data Store

The SQLite DB is the primary source of truth. The Obsidian vault is derived
from it and can be fully reconstructed if sync conflicts corrupt markdown files.

### 5a — Core state (dedup + sync tracking)
- [ ] Create `synced_screenshots` table (see schema below)
- [ ] Implement `SyncState`: `is_path_synced()`, `is_synced()`, `mark_synced()`
- [ ] Test idempotency (multiple runs must not duplicate rows)
- [ ] Test content‑hash keying

### 5b — Content storage
`synced_screenshots` schema (full backup fields):

| Column | Type | Notes |
|--------|------|-------|
| `id` | INTEGER PK | |
| `device_path` | TEXT | Path on X4 SD card |
| `content_hash` | TEXT | SHA-256 of original BMP |
| `book_title` | TEXT | |
| `sync_date` | TEXT | YYYY-MM-DD (from mtime) |
| `synced_at` | TIMESTAMP | |
| `png_data` | BLOB | Full PNG bytes — DB is self-contained backup |
| `ocr_text` | TEXT | Raw Tesseract output |
| `ocr_corrected` | TEXT | User-edited correction of OCR text |
| `user_notes` | TEXT | Free-form notes per screenshot |

- [ ] Add `png_data`, `ocr_corrected`, `user_notes` columns to table
- [ ] Store PNG bytes in DB on every sync (alongside writing file to vault)
- [ ] Test: DB row present after sync with non-null png_data

### 5c — CRUD API
FastAPI endpoints (no new dep — FastAPI already required):

- [ ] `GET /api/books` — list unique books with screenshot counts
- [ ] `GET /api/books/{book}/screenshots` — list screenshots (metadata, no blob)
- [ ] `GET /api/screenshots/{id}/image` — serve PNG from DB as `image/png`
- [ ] `PUT /api/screenshots/{id}` — update `ocr_corrected` and/or `user_notes`
- [ ] `GET /api/reading-log` — list `progress_updates` entries
- [ ] `POST /api/vault/export` — re-generate all vault markdown from DB rows

### 5d — Web frontend
Single HTML file served by FastAPI at `/app`. Vanilla JS + `fetch()`, no build step, no framework.

- [ ] Book list with screenshot counts
- [ ] Screenshot view per book: image (from `/api/screenshots/{id}/image`), OCR text, notes
- [ ] Inline edit for `ocr_corrected` and `user_notes` with save button
- [ ] Reading log tab
- [ ] "Export vault" button (calls `POST /api/vault/export`)

## Phase 6: KOReader Sync Server
- [ ] Implement minimal `POST /syncs/progress` endpoint
- [ ] Implement `GET /syncs/progress` endpoint
- [ ] Create SQLite `progress_updates` table
- [ ] Test with X4 KOReader Sync settings pointing to local server
- [ ] Enable "Send Document Metadata" on X4
- [ ] Verify progress data arrives correctly

## Phase 7: Vault Writer Integration
- [ ] Implement `VaultWriter.write_reading_log()`
- [ ] Implement `VaultWriter.update_book_timeline()`
- [ ] Test by sending sample progress updates
- [ ] Verify frontmatter in book notes works with Obsidian
- [ ] Add Dataview‑friendly frontmatter (status, last_sync)

## Phase 8: Observability
- [ ] Implement ntfy.sh notifications
- [ ] Implement Home Assistant webhook notifications
- [ ] Create FastAPI JSON status endpoint (`/status`)
- [ ] Display: last sync time, books touched today, total screenshots, recent errors
- [ ] Add logging throughout all components

## Phase 9: Integration & Polish
- [ ] Wire all components together in `main.py`
- [ ] Document deployment steps (Docker-first)
- [ ] Write unit tests for core modules
- [ ] Test on actual homelab hardware

## Known Issues to Watch For

- [ ] mDNS resolution (`crosspoint.local`) may fail in Docker without `network_mode: host`
- [ ] File Transfer mode times out after idle minutes — poll window is small
- [ ] WebSocket connection may drop — need to handle reconnects
- [ ] Book titles with special characters need sanitization for filenames
- [ ] FAT filesystem timestamps may not be reliable — use current time as fallback
- [ ] OCR accuracy varies with e‑ink font rendering and illustrations —
      treat it as a best‑effort search aid, not an authoritative transcript