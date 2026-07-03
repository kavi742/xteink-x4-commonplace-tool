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
  ok   - pytest (3 passed)
== network (--network host) ==
  ok   - crosspoint.local resolves inside container
  ok   - GET http://crosspoint.local/api/status → 200
== live watcher (15s timeout) ==
  ok   - X4 detected at crosspoint.local

Phase 2: all checks passed
```

If `crosspoint.local` doesn't resolve inside the container, install
`libnss-mdns` on the Debian host (`sudo apt install libnss-mdns`) or pass
the X4's IP directly as the argument.
