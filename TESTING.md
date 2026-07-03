# Testing Guide

Tests are split into **automated** (run in CI or locally without hardware) and
**live** (require the physical X4 device). Each phase lists both.

---

## Phase 1 — Foundation

### Automated: shell smoke test

Checks vault folder structure, `.stignore`, `.gitignore`, and the Docker build.

**Requires:** bash (WSL or Linux/macOS), optionally `docker`

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
```

### Live: none

Phase 1 has no hardware dependency.

---

## Phase 2 — Device Discovery

### Automated: unit tests

Verifies poll-and-return logic and offline detection without needing the device.

**Requires:** Python venv with `pytest`, `pytest-asyncio`, `aiohttp`

```bash
# First time only — corporate SSL requires trusted-host flags on AMD network:
pip install pytest pytest-asyncio aiohttp \
    --trusted-host pypi.org \
    --trusted-host pypi.python.org \
    --trusted-host files.pythonhosted.org

# Run tests:
python -m pytest tests/test_watcher.py -v
```

Expected output:

```
tests/test_watcher.py::test_poll_returns_host_on_200 PASSED
tests/test_watcher.py::test_wait_for_offline_returns_on_non_200 PASSED
tests/test_watcher.py::test_wait_for_offline_returns_on_connection_error PASSED
3 passed
```

### Live: device detection

**Requires:** X4 powered on and connected to the same network (or Tailscale).

1. Put the X4 into **File Transfer mode** (press the File Transfer button).
2. Run from the repo root:

   ```bash
   # Default hostname:
   python xteink_service/watcher.py

   # Or with Tailscale IP:
   python xteink_service/watcher.py 100.x.x.x
   ```
3. Expected log output:

   ```
   Watching for X4 at crosspoint.local (poll every 5s)...
   X4 online at crosspoint.local
   ```
4. Exit File Transfer mode on the device; the process will have already returned
   (it exits as soon as the device responds). Re-run to verify `wait_for_offline`
   by pressing Ctrl-C after detection or letting it be called from `main.py` once
   that module exists.

**Pass criteria:** the "X4 online" line appears within ~10 seconds of pressing
File Transfer, and the correct hostname/IP is printed.

### Live: device detection from inside the Docker container (Debian homelab server)

**Requires:** Docker installed on the Debian server, X4 in File Transfer mode.

`--network host` on Linux gives the container the host's full network stack —
avahi-daemon for `crosspoint.local` resolution and the Tailscale interface if
installed.

```bash
# Build the image
docker build -t xteink-service:dev .

# Run the watcher inside the container
docker run --rm --network host xteink-service:dev \
  python xteink_service/watcher.py

# Or with a Tailscale IP (when X4 is on mobile hotspot):
docker run --rm --network host xteink-service:dev \
  python xteink_service/watcher.py 100.x.x.x
```

Expected output is identical to the local test:
```
Watching for X4 at crosspoint.local (poll every 5s)...
X4 online at crosspoint.local
```
