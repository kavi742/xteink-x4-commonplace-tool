# Testing Guide

All testing runs inside Docker on the Debian homelab server.
`--network host` gives the container the host's full network stack —
avahi-daemon for `crosspoint.local` resolution and Tailscale if installed.

---

## Phase 1 — Foundation

Checks vault folder structure, `.stignore`, `.gitignore`, and the Docker build.

**Requires:** bash on the server

```bash
bash test_scripts/test-phase1.sh
```

Expected output:

```
== vault structure ==
  ok   - Reading Log/ exists
  ok   - Books/ exists
  ok   - .stignore exists
  ok   - .stignore ignores Obsidian workspace
  ok   - init-vault.sh is idempotent (second run exits 0)
== .gitignore ==
  ok   - vault/ is git-ignored
  ok   - state/ is git-ignored
  ok   - .env is git-ignored
  ok   - uv.lock is NOT ignored
== docker ==
  ok   - docker-compose.yml is valid config
  ok   - Dockerfile builds
  ok   - tesseract-ocr is installed in the image
  ok   - all Python deps import in the image
```

---

## Phase 2 — Device Discovery

```bash
# Default hostname (crosspoint.local):
bash test_scripts/test-phase2.sh

# With a static IP (bypasses mDNS — useful if avahi isn't set up):
bash test_scripts/test-phase2.sh 192.168.x.x
```

The script runs in order: build → unit tests → DNS resolution check →
`GET /api/status` check → live watcher (15s timeout). Each step prints
`ok` or `FAIL`. The live watcher step requires the X4 to be reachable.

Expected output (device reachable and responding):
```
== build ==
  ok   - image built (xteink-service:dev)
== unit tests ==
  ok   - pytest
== network ==
  resolved crosspoint.local → 192.168.x.x
  ok   - GET http://crosspoint.local/api/status → 200
== live watcher (15s timeout) ==
  ok   - X4 detected at crosspoint.local

Phase 2: all checks passed
```

Hostname resolution uses avahi on the host and injects the IP into the container
via `--add-host`, so in-container mDNS is not required.

---

## Phase 3 — Sync Status (server-side)

Status is logged server-side, not shown on the device: port 81's
`START:name:size:path` is a file-upload channel, not a display (see
ARCHITECTURE.md §3). Unit tests cover the log-only `show()` and the device
junk-file cleanup:

```bash
docker run --rm xteink-service:dev python -m pytest tests/test_status_display.py -v
```

Expected: 4 passing —
```
test_show_logs_and_never_touches_device PASSED
test_cleanup_deletes_only_zero_byte_root_files PASSED
test_cleanup_handles_unreachable_device PASSED
test_cleanup_counts_only_successful_deletes PASSED
```

**Pass criteria:** `show()` logs `X4 status: <msg>` and never opens a device
connection; `cleanup_device_junk()` deletes only 0-byte files at the device root.
