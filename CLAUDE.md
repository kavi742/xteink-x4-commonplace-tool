# Xteink X4 Commonplace Tool — Agent Notes

## Project

Homelab service (Python + Docker, Debian server) that:
- **Screenshot archiving**: when X4 enters File Transfer mode, downloads all BMP screenshots, converts to PNG, OCRs with Tesseract, writes to Obsidian vault
- **KOReader sync server**: receives reading progress from X4 via kosync protocol
- **On-device status display**: shows sync progress on the X4's Calibre Wireless screen

**Stack**: Python 3.12, FastAPI, aiohttp, Pillow, pytesseract, aiosqlite, websockets. Docker on Debian homelab. Obsidian vault synced via Syncthing.

---

## Architecture decisions

- **No mDNS** (removed `python-zeroconf`); simple HTTP polling of `crosspoint.local/api/status`
- **No pyyaml/jinja2**; config via env vars, status endpoint returns JSON
- **`network_mode: host`** in docker-compose — shares host's Tailscale interface and mDNS resolver
- **Books go into `Books/<title>.md`** under `## YYYY-MM-DD` date headings (not a separate `Commonplace/` tree); `Books/<title>/` folder holds attachment PNGs
- **DB is primary source of truth** — vault markdown is derived and rebuildable
- **JSON sidecars** — each PNG gets a `.json` sidecar (`Books/<book>/<date>-NN.json`) written before the DB insert, containing `device_path`, `content_hash`, `book_title`, `sync_date`, `ocr_text`, `archived_at`. Provides a DB-independent backup so the vault folder is fully self-contained.
- **Progress bar**: `START:message:size:/` with actual BMP bytes streamed so bar fills correctly; `START:message:0:/` for text-only status messages
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
  status_display.py  -- x4_status() async context manager
  archiver.py        -- ScreenshotArchiver (run_sync + helpers)
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
| 3 — Status display | done | `x4_status()` async context manager, `show(msg, data=bytes)` |
| 4 — Screenshot archiving | done | `_list_screenshots`, `_download_file`, `_bmp_to_png`, `_ocr_image`, `_embed_ocr_in_png`, `VaultWriter`, `run_sync()`. 25 screenshots archived in live e2e test |
| 5 — KOReader sync server | done | FastAPI, `POST/PUT/GET /syncs/progress`, auth stubs, `ProgressStore`, live test confirmed |
| 6a — Dedup state | done | `SyncState`, `synced_screenshots` + `document_aliases` tables, wired into `run_sync()` |
| 6b — Document aliases | done | `md5(filename)` is the sync hash. `alias.py --scan` resolves all hashes from file listing, no downloads. Manual fallback: `alias.py <hash> "Title"` |
| 7 — Vault writer integration | done | KOReader progress wired into `write_reading_log` / `update_book_timeline`, interleaved under the same `## YYYY-MM-DD` headings as screenshots |
| 8 — Observability | done | ntfy.sh notifications + FastAPI `/status` endpoint |
| 9 — Full data store + web UI | done | CRUD API + SvelteKit web UI at `/app`; images served from the vault via `vault_png_path` (no DB blobs); OCR corrections, user notes, highlights, TBR, alias management |
| 10 — Integration | done | `main.py` + `__main__.py` run the watcher loop and KOReader server together; `python -m xteink_service` works |

---

## Known constraints

- **No "Send Document Metadata"** in CrossPoint firmware — KOReader sync hashes are opaque binary hashes of epub content. Algorithm confirmed from firmware source: partial MD5, 12 offsets `[0, 1024, 4096, 16384, ...]`. Existing books have Calibre-modified files so hashes don't match current on-disk files; manual mapping needed in Phase 9 web UI.
- **KOReader sync `document` field is `md5(filename)`** — NOT the partial content hash. `alias.py --scan` resolves all unresolved hashes instantly from the device file listing alone (no downloads). Confirmed 2026-07-04.
- **Calibre Wireless mode** (`/api/files`, `/download`, WebSocket port 81) is separate from KOReader sync (port 8090). They are independent features.
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
