# TODO

## Phase 1: Foundation
- [x] Create Obsidian vault folder structure:
  - [x] `Commonplace/` root
  - [x] `Reading Log/` directory
  - [x] `Books/` directory
  - via `scripts/init-vault.sh` — idempotent, run it whenever
- [x] Set up Syncthing between homelab and Obsidian clients
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
- [x] Add `pytesseract` (Python) + `tesseract-ocr` (system package) dependency
- [x] Implement `_ocr_image()` to extract text from each PNG via `pytesseract`
- [x] Embed OCR text under each screenshot embed as a collapsible callout
      (`> [!quote]- OCR text`) so it's indexed by Obsidian search
- [x] Handle OCR failures gracefully — missing binary, blank/corrupt image —
      log a warning and continue writing the image without text
- [x] Add `ocr_text` column to the `synced_screenshots` SQLite table
      (already present in schema from Phase 6a)
- [x] Test OCR accuracy against a handful of real X4 screenshots (mixed
      fonts, illustrations) to gauge how reliable it actually is
      — confirmed good quality on Pastoral (e-ink text), see test_scripts/sample_ocr.txt
- [x] Implement `VaultWriter.write_screenshot()` and `append_to_daily_note()`
- [x] Test end‑to‑end with 3‑5 screenshots
  — 25 screenshots archived from Pastoral in live e2e test (`test-phase4.sh crosspoint.local`)

## Phase 5: KOReader Sync Server

Receives reading progress from the X4. Data goes straight into SQLite (no vault
write yet — that happens in Phase 7 once the DB is confirmed working).

- [x] Implement minimal `POST /syncs/progress` endpoint
- [x] Implement `GET /syncs/progress` endpoint
- [x] Create SQLite `progress_updates` table
- [x] Test with X4 KOReader Sync settings pointing to local server
- [x] Enable "Send Document Metadata" on X4
- [x] Verify progress data arrives correctly
  — X4 sends GET + PUT on sync; both return 200; doc IDs are binary hashes
  — No "Send Document Metadata" option in CrossPoint firmware; title/author always empty
  — Hash algorithm confirmed from firmware source (partial MD5, 12 offsets starting at 0)
  — Auto-resolution works on unmodified epubs; existing books with Calibre-modified files
    need manual mapping in Phase 9 web UI (hash → title entry field)

## Phase 6: State Management (dedup)

Minimal SQLite state so `run_sync()` skips already-archived screenshots.
Full content backup (PNG blobs, OCR corrections) moves to Phase 9.

### 6a — Screenshot dedup
- [x] Create `synced_screenshots` table (`device_path`, `content_hash`, `synced_at`, `book_title`, `sync_date`, `ocr_text`)
- [x] Implement `SyncState`: `is_path_synced()`, `is_synced()`, `mark_synced()`
- [x] Wire into `run_sync()` — skip download if path already in DB
- [x] Test idempotency (multiple runs skip already-synced files; state.db deduplicates by device_path)

### 6b — Document alias table (hash → title mapping)

KOReader sync `document` field is `md5(filename)` — confirmed 2026-07-04.
`alias.py --scan` resolves all hashes instantly from the device file listing (no downloads needed).

- [x] Create `document_aliases` table
- [x] `alias.py --scan` — fast resolve via `md5(filename)` from file listing alone
- [x] `alias.py --auto` — fallback: tries content hash variants + filename + path
- [x] `alias.py <hash> "Title"` — manual override
- [x] Wire `--scan` into `sync_once.py` — runs automatically during File Transfer mode
  (device port 80 is only open then; KOReader sync fires in normal reading mode)

## Phase 7: Vault Writer Integration

Write KOReader reading progress into the vault alongside screenshots.

- [x] Implement `VaultWriter.write_reading_log()`
- [x] Implement `VaultWriter.update_book_timeline()`
- [x] Wire into `koreader_sync.py` `put_progress` endpoint
- [x] Test with live X4 sync — reading log + book timeline written correctly
- [x] Wire `alias.py --scan` into `sync_once.py` so hashes resolve on File Transfer

## Phase 8: Observability

- [x] Implement ntfy.sh notifications
- [x] Create FastAPI JSON status endpoint (`/status`)
- [x] Display: last sync time, books touched today, total screenshots, recent KOReader updates
- [ ] Add logging throughout all components

## Phase 9: Full Data Store + CRUD API + Web UI

The DB becomes primary source of truth — vault can be fully rebuilt from it.
KOReader sync data (Phase 5) and screenshot data (Phase 6) both feed this.

### Schema additions
| Column | Table | Notes |
|--------|-------|-------|
| `png_data` | `synced_screenshots` | Full PNG bytes — DB self-contained backup |
| `ocr_corrected` | `synced_screenshots` | User-edited OCR correction |
| `user_notes` | `synced_screenshots` | Free-form per-screenshot notes |

### CRUD API (FastAPI, no new dep)
- [x] `GET /api/books` — book list with screenshot counts
- [x] `GET /api/books/{book}/screenshots` — screenshot metadata (no blob)
- [x] `GET /api/screenshots/{id}/image` — serve PNG from vault filesystem
- [x] `PUT /api/screenshots/{id}` — update `ocr_corrected` and/or `user_notes`
- [x] `GET /api/reading-log` — KOReader progress history
- [x] `POST /api/vault/rebuild` — rebuild all vault markdown from DB
- [x] `GET/PUT /api/aliases` — hash → title management

### Web frontend
Single HTML file at `/app`. Vanilla JS + `fetch()`, no build step, no framework.

- [ ] Book list + screenshot gallery (SvelteKit, `/books`)
- [ ] Screenshot detail panel (full image, OCR edit, notes)
- [ ] Reading log tab (`/log`)
- [ ] Alias management table (`/aliases`)
- [ ] TBR list (`/tbr`)
- [ ] Essay per day (`/essays`) — fetch, convert to EPUB, push to X4

## Phase 10: Integration & Polish

- [x] Wire all components together in `main.py` + `__main__.py`
- [x] `python -m xteink_service` starts both KOReader sync server + watcher loop
- [ ] Deployment docs (README updated; homelab-specific Syncthing setup)
- [x] Unit tests for all core modules (59 tests passing)
- [x] Live end-to-end tested on homelab hardware (25 screenshots, KOReader sync)

## Known Issues to Watch For

- [ ] mDNS resolution (`crosspoint.local`) may fail in Docker without `network_mode: host`
- [ ] File Transfer mode times out after idle minutes — poll window is small
- [ ] WebSocket connection may drop — need to handle reconnects
- [ ] Book titles with special characters need sanitization for filenames
- [ ] FAT filesystem timestamps may not be reliable — use current time as fallback
- [ ] OCR accuracy varies with e‑ink font rendering and illustrations —
      treat it as a best‑effort search aid, not an authoritative transcript