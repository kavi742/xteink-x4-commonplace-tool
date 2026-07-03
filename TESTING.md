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

### Automated: unit tests (in container)

```bash
docker build -t xteink-service:dev .
docker run --rm xteink-service:dev python -m pytest tests/test_watcher.py -v
```

Expected output:

```
tests/test_watcher.py::test_poll_returns_host_on_200 PASSED
tests/test_watcher.py::test_wait_for_offline_returns_on_non_200 PASSED
tests/test_watcher.py::test_wait_for_offline_returns_on_connection_error PASSED
3 passed
```

### Live: device detection (in container, X4 required)

**Requires:** X4 in File Transfer mode, on the same network or Tailscale.

```bash
# Default hostname:
docker run --rm --network host xteink-service:dev \
  python xteink_service/watcher.py

# Tailscale (X4 on mobile hotspot):
docker run --rm --network host xteink-service:dev \
  python xteink_service/watcher.py 100.x.x.x
```

Expected output:
```
Watching for X4 at crosspoint.local (poll every 5s)...
X4 online at crosspoint.local
```

**Pass criteria:** "X4 online" appears within ~10 seconds of pressing File Transfer.
