# TODO

## Phase 1: Foundation
- [ ] Create Obsidian vault folder structure:
  - [ ] `Commonplace/` root
  - [ ] `Reading Log/` directory
  - [ ] `Books/` directory
- [ ] Set up Syncthing between homelab and Obsidian clients
- [ ] Configure Syncthing to ignore temporary files
- [ ] **Create Docker setup (Dockerfile, docker-compose.yml)** ← PRIMARY DEPLOYMENT METHOD

## Phase 2: Device Discovery
- [ ] **Implement mDNS service discovery using python-zeroconf**
  - [ ] Subscribe to `_http._tcp.local.` service type
  - [ ] Handle `add_service` and `remove_service` events
  - [ ] Resolve service to get IP address
- [ ] Add fallback to polling `http://crosspoint.local/api/status`
- [ ] Log device detection events
- [ ] Test with actual X4 in File Transfer mode

## Phase 3: On‑Device Status Display
- [ ] Manually connect to `ws://crosspoint.local:81/`
- [ ] Send `START:Test message:1:/` and verify it appears on X4 screen
- [ ] Implement `XteinkStatus` class in Python
- [ ] Test `connect()`, `show()`, `done()`, `close()`
- [ ] Add graceful degradation if WebSocket fails

## Phase 4: Screenshot Archiving
- [ ] Implement `_list_screenshots()` using `/api/files`
- [ ] Test grouping by book folder and day (mtime)
- [ ] Implement `_download_file()` using `/download`
- [ ] Implement `_bmp_to_png()` conversion with Pillow
- [ ] Implement `VaultWriter.write_screenshot()` and `append_to_daily_note()`
- [ ] Test end‑to‑end with 3‑5 screenshots

## Phase 5: State Management
- [ ] Create SQLite `synced_screenshots` table
- [ ] Implement `is_synced()` and `mark_synced()`
- [ ] Test idempotency (multiple runs shouldn't duplicate)
- [ ] Verify content‑hashing works correctly

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
- [ ] Create FastAPI status page with Jinja template
- [ ] Display: last sync time, books touched today, total screenshots, recent errors
- [ ] Add logging throughout all components

## Phase 9: Integration & Polish
- [ ] Wire all components together in `main.py`
- [ ] Create configuration file (settings.yaml)
- [ ] Document deployment steps (Docker-first)
- [ ] Write unit tests for core modules
- [ ] Test on actual homelab hardware

## Phase 10: Optional Enhancements
- [ ] Add support for Nginx Proxy Manager (HTTPS)
- [ ] Add Tailscale integration guide
- [ ] Support multiple X4 devices
- [ ] Add "delete after sync" option (off by default)
- [ ] Add custom note templates (with frontmatter)
- [ ] Export reading statistics to CSV/JSON
- [ ] Add Obsidian Dataview queries for reading dashboard

## Known Issues to Watch For

- [ ] mDNS resolution (`crosspoint.local`) may fail in Docker without `network_mode: host`
- [ ] File Transfer mode times out after idle minutes — poll window is small
- [ ] WebSocket connection may drop — need to handle reconnects
- [ ] Book titles with special characters need sanitization for filenames
- [ ] FAT filesystem timestamps may not be reliable — use current time as fallback