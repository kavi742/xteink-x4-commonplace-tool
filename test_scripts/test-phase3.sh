#!/usr/bin/env bash
# Phase 3 test: on-device status display (x4_status WebSocket context manager)
# Run from anywhere: bash test_scripts/test-phase3.sh [host] [message]

set -uo pipefail
FAIL=0
HOST="${1:-crosspoint.local}"
MSG="${2:-Hello from xteink-service}"
IMAGE="xteink-service:dev"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

pass() { echo "  ok   - $1"; }
fail() { echo "  FAIL - $1"; FAIL=1; }

# Resolve hostname on the host so the container can reach it.
if [[ "$HOST" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  DEVICE_IP="$HOST"
  ADD_HOST=""
else
  DEVICE_IP=$(getent hosts "$HOST" 2>/dev/null | awk '{print $1}')
  if [ -z "$DEVICE_IP" ]; then
    echo "  warn - could not resolve $HOST; using hostname directly via --network host"
    DEVICE_IP="$HOST"
    ADD_HOST=""
  else
    ADD_HOST="--add-host $HOST:$DEVICE_IP"
  fi
fi

echo "== build =="
if docker build -t "$IMAGE" "$REPO_ROOT"; then
  pass "image built ($IMAGE)"
else
  fail "docker build failed"
  exit 1
fi

echo "== unit tests =="
if docker run --rm "$IMAGE" python -m pytest tests/test_status_display.py -q; then
  pass "pytest"
else
  fail "pytest"
fi

echo "== graceful degradation =="
# Points at localhost:81 where nothing is listening (or returns non-WS).
# Must exit 0 AND log the 'Could not connect' warning.
deg_out=$(docker run --init --rm --network host "$IMAGE" \
    python xteink_service/status_display.py 127.0.0.1 "degradation-test" 2>&1)
deg_exit=$?
if [ $deg_exit -eq 0 ] && echo "$deg_out" | grep -q "Could not connect"; then
  pass "exits 0 and logs warning on connection failure"
else
  echo "$deg_out"
  fail "expected exit 0 + 'Could not connect' warning (got exit $deg_exit)"
fi

echo "== live: send message to X4 screen (device required) =="
echo "  sending: $MSG"
echo "  resolved $HOST → $DEVICE_IP"
if docker run --init --rm --network host $ADD_HOST "$IMAGE" \
    python xteink_service/status_display.py "$HOST" "$MSG"; then
  pass "message sent — verify it appeared on X4 screen"
else
  fail "WebSocket send failed"
fi

echo
[ "$FAIL" -eq 0 ] && echo "Phase 3: all checks passed" || echo "Phase 3: one or more checks FAILED"
exit "$FAIL"
