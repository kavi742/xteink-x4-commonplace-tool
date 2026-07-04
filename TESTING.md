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
  ok   - Commonplace/ exists
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

## Phase 3 — On-Device Status Display

```bash
# Default hostname and message:
bash test_scripts/test-phase3.sh

# Custom host or message:
bash test_scripts/test-phase3.sh crosspoint.local "Syncing screenshots..."
```

Runs in order: build → unit tests → graceful degradation check (no device needed)
→ live message send (device must be in Calibre Wireless mode).

Expected output:
```
== build ==
  ok   - image built (xteink-service:dev)
== unit tests ==
  ok   - pytest
== graceful degradation ==
  ok   - exits 0 when WebSocket connection is refused
== live: send message to X4 screen (device required) ==
  sending: Hello from xteink-service
  resolved crosspoint.local → 192.168.x.x
  ok   - message sent — verify it appeared on X4 screen

Phase 3: all checks passed
```

**Pass criteria:** the message text appears in the Calibre Wireless status area on
the X4 screen for ~5 seconds.
