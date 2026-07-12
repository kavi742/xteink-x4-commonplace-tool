# Xteink X4 Commonplace Tool — Agent Notes

## Project

Homelab service (Python + Docker, Debian server) that:
- **Screenshot archiving**: when X4 enters File Transfer mode, downloads all BMP screenshots, converts to PNG, OCRs with Tesseract, writes to Obsidian vault
- **KOReader sync server**: receives reading progress from X4 via kosync protocol
- **Page-count lookup**: on File Transfer, looks up each read book's page count (Open Library, with an epub word-count fallback) so reading percentages become page numbers
- **Sync status**: logged server-side (`docker compose logs`); see the note below on why it is no longer shown on the device

**Stack**: Python 3.12, FastAPI, aiohttp, Pillow, pytesseract, aiosqlite. Docker on Debian homelab. Obsidian vault synced via Syncthing.

---

## Architecture decisions

- **No mDNS** (removed `python-zeroconf`); simple HTTP polling of `crosspoint.local/api/status`
- **No pyyaml/jinja2**; config via env vars, status endpoint returns JSON
- **`network_mode: host`** in docker-compose — shares host's Tailscale interface and mDNS resolver
- **Books go into `Books/<title>.md`** under `## YYYY-MM-DD` date headings (not a separate `Commonplace/` tree); `Books/<title>/` folder holds attachment PNGs
- **DB is primary source of truth** — vault markdown is derived and rebuildable
- **JSON sidecars** — each PNG gets a `.json` sidecar (`Books/<book>/<date>-NN.json`) written before the DB insert, containing `device_path`, `content_hash`, `book_title`, `sync_date`, `ocr_text`, `archived_at`. Provides a DB-independent backup so the vault folder is fully self-contained.
- **Sync status is server-side only** — port 81's `START:name:size:path` protocol is the device's *file-upload* channel, not a status display: sending text through it saved a 0-byte junk file named after each message at the device root. `status_display.x4_status()` now just logs (`X4 status: <msg>`); `cleanup_device_junk()` deletes any leftover junk files during File Transfer.
- **Page counts** — `book_pages` table (title → total_pages, source) is populated during File Transfer by `book_pages.resolve_book_pages()`: Open Library `number_of_pages_median` first (sanitize the query — `-` is a Lucene NOT operator), epub word-count estimate (`pages.py`) as fallback. `pages.page_at(pct, total)` turns a KOReader percentage into a page number.
- **Service port is 8090** (8081 was occupied on the homelab)
- **Docker `--network host`** required so container inherits host's Tailscale and mDNS
- **X4 fonts have no emoji** — all status messages must use plain ASCII

---

## Vault structure

```
vault/
  Books/
    Pastoral.md          <- screenshots + KOReader progress, interleaved by date
    Pastoral/
      2026-07-04-01.png  <- PNG with OCR text in iTXt metadata
  Reading Log/
    YYYY-MM-DD.md        <- daily diary (Phase 7)
```

Daily note format:

```markdown
## 2026-07-04

![[Pastoral/2026-07-04-01.png]]
> [!quote]- OCR text
> Apple. Now, because Bigland did not get home...
```

---

## Key files

```
xteink_service/
  watcher.py         -- poll_for_device(), wait_for_offline()
  status_display.py  -- x4_status() (server-side log) + cleanup_device_junk()
  archiver.py        -- ScreenshotArchiver (run_sync + helpers)
  book_pages.py      -- Open Library / word-count page-count lookup + cache
  pages.py           -- epub word-count -> page estimate, page_at()
  vault_writer.py    -- VaultWriter (write_screenshot, append_to_daily_note, etc.)
  state.py           -- SyncState (dedup + document_aliases tables)
  koreader_sync.py   -- FastAPI KOReader sync server
  document_id.py     -- CrossPoint partial-MD5 hash algorithm (from firmware source)
  hash_books.py      -- browses device books, computes hashes for alias resolution
  capture.py         -- single-shot test: download one screenshot + OCR
  sync_once.py       -- single sync cycle entry point
tests/
  test_watcher.py, test_status_display.py, test_archiver.py,
  test_vault_writer.py, test_state.py, test_koreader_sync.py, test_document_id.py
test_scripts/
  test-phase{1-5}.sh, test-koreader.sh, test-document-hash.sh, capture-screenshot.sh
```

---

## Phase status

| Phase | Status | Notes |
|-------|--------|-------|
| 1 — Foundation | done | Docker, vault init script, .gitignore |
| 2 — Device discovery | done | `poll_for_device()`, `wait_for_offline()`, 3 unit tests |
| 3 — Status display | done | `x4_status()` logs server-side. Port 81 turned out to be a file-upload channel (created junk files), so on-device status was removed; `cleanup_device_junk()` clears leftovers |
| 4 — Screenshot archiving | done | `_list_screenshots`, `_download_file`, `_bmp_to_png`, `_ocr_image`, `_embed_ocr_in_png`, `VaultWriter`, `run_sync()`. 25 screenshots archived in live e2e test |
| 5 — KOReader sync server | done | FastAPI, `POST/PUT/GET /syncs/progress`, auth stubs, `ProgressStore`, live test confirmed |
| 6a — Dedup state | done | `SyncState`, `synced_screenshots` + `document_aliases` tables, wired into `run_sync()` |
| 6b — Document aliases | done | `md5(filename)` is the sync hash. `alias.py --scan` resolves all hashes from file listing, no downloads. Manual fallback: `alias.py <hash> "Title"` |
| 7 — Vault writer integration | done | KOReader progress wired into `write_reading_log` / `update_book_timeline`, interleaved under the same `## YYYY-MM-DD` headings as screenshots |
| 8 — Observability | done | ntfy.sh notifications + FastAPI `/status` endpoint |
| 9 — Full data store + web UI | done | CRUD API + SvelteKit web UI at site root `/`; images served from the vault via `vault_png_path` (no DB blobs); OCR corrections, user notes, highlights, TBR, alias management |
| 9b — Reading calendar + pages | done | Per-book reading calendar/heatmap; page counts (Open Library + epub estimate) surfaced as page numbers in the reading log, per-book stats (`/api/books/{slug}/reading-stats`), and calendar tooltips |
| 10 — Integration | done | `main.py` + `__main__.py` run the watcher loop and KOReader server together; `python -m xteink_service` works |

---

## Known constraints

- **No "Send Document Metadata"** in CrossPoint firmware — KOReader sync hashes are opaque binary hashes of epub content. Algorithm confirmed from firmware source: partial MD5, 12 offsets `[0, 1024, 4096, 16384, ...]`. Existing books have Calibre-modified files so hashes don't match current on-disk files; manual mapping needed in Phase 9 web UI.
- **KOReader sync `document` field is `md5(filename)`** — NOT the partial content hash. `alias.py --scan` resolves all unresolved hashes instantly from the device file listing alone (no downloads). Confirmed 2026-07-04.
- **Calibre Wireless mode** (`/api/files`, `/download`, WebSocket port 81) is separate from KOReader sync (port 8090). They are independent features. Port 81's `START:name:size:path` is a file-**upload** channel (not a status display), so the service no longer sends status to it.
- `crosspoint.local` mDNS does not resolve inside Docker without `network_mode: host`. Use `DEVICE_HOST=<static IP>` or `extra_hosts` as fallback.
- FAT filesystem timestamps on the device may not be reliable — use current time as fallback for grouping screenshots by day.

---

## Running tests

```bash
uv run pytest
```

## Running a one-shot sync (against a live device)

```bash
uv run python -m xteink_service.sync_once <host> <vault_path>
```

## Capturing a single screenshot for testing

```bash
uv run python -m xteink_service.capture <host>
```
