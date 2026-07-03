#!/usr/bin/env bash
# Phase 2 test: device discovery (poll_for_device + wait_for_offline)
# Run from anywhere: bash test_scripts/test-phase2.sh [host]
# HOST defaults to crosspoint.local; pass an IP to bypass mDNS.

set -uo pipefail
FAIL=0
HOST="${1:-crosspoint.local}"
IMAGE="xteink-service:dev"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

pass() { echo "  ok   - $1"; }
fail() { echo "  FAIL - $1"; FAIL=1; }

echo "== build =="
if docker build -t "$IMAGE" "$REPO_ROOT"; then
  pass "image built ($IMAGE)"
else
  fail "docker build failed"
  exit 1
fi

echo "== unit tests =="
if docker run --rm "$IMAGE" python -m pytest tests/test_watcher.py -q; then
  pass "pytest"
else
  fail "pytest"
fi

echo "== network (--network host) =="
if docker run --rm --network host "$IMAGE" \
    python -c "import socket; socket.gethostbyname('$HOST')"; then
  pass "$HOST resolves inside container"
else
  fail "$HOST does not resolve (try: sudo apt install libnss-mdns, or pass IP as arg)"
fi

if docker run --rm --network host "$IMAGE" \
    python -c "import urllib.request; urllib.request.urlopen('http://$HOST/api/status', timeout=3)"; then
  pass "GET http://$HOST/api/status → 200"
else
  fail "GET http://$HOST/api/status failed (device reachable? File Transfer mode active?)"
fi

echo "== live watcher (15s timeout) =="
echo "  polling $HOST — Ctrl-C to abort..."
if timeout 15 docker run --init --rm --network host "$IMAGE" \
    python xteink_service/watcher.py "$HOST"; then
  pass "X4 detected at $HOST"
else
  fail "X4 not detected within 15s"
fi

echo
[ "$FAIL" -eq 0 ] && echo "Phase 2: all checks passed" || echo "Phase 2: one or more checks FAILED"
exit "$FAIL"
exit "$FAIL"
