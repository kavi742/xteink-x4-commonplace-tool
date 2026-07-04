#!/usr/bin/env bash
# Phase 5 test: KOReader sync server
# Run from anywhere: bash test_scripts/test-phase5.sh [server_url]
# SERVER_URL defaults to http://localhost:8090 (for live server test).

set -uo pipefail
FAIL=0
SERVER_URL="${1:-http://localhost:8090}"
IMAGE="xteink-service:dev"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

pass() { echo "  ok   - $1"; }
fail() { echo "  FAIL - $1"; FAIL=1; }

echo "== build =="
if docker build -t "$IMAGE" "$REPO_ROOT"; then
  pass "image built ($IMAGE)"
else
  fail "docker build failed"; exit 1
fi

echo "== unit tests =="
if docker run --rm "$IMAGE" python -m pytest tests/test_koreader_sync.py -v; then
  pass "pytest"
else
  fail "pytest"
fi

echo "== live server test (server must be running at $SERVER_URL) =="
if ! curl -sf "$SERVER_URL/users/auth" > /dev/null 2>&1; then
  echo "  skip - no server at $SERVER_URL"
  echo "         Start with: docker run --rm -p 8090:8090 $IMAGE python -m uvicorn xteink_service.koreader_sync:app --host 0.0.0.0 --port 8090"
else
  # Auth
  r=$(curl -sf "$SERVER_URL/users/auth"); echo "$r"
  echo "$r" | grep -q '"authorized"' && pass "GET /users/auth" || fail "GET /users/auth"

  # POST progress
  r=$(curl -sf -X POST "$SERVER_URL/syncs/progress" \
    -H "Content-Type: application/json" \
    -d '{"document":"Pastoral.epub","progress":"0/Ch8","percentage":22.5,"device":"test"}')
  echo "$r"
  echo "$r" | grep -q '"Pastoral.epub"' && pass "POST /syncs/progress" || fail "POST /syncs/progress"

  # GET progress
  r=$(curl -sf "$SERVER_URL/syncs/progress/Pastoral.epub")
  echo "$r"
  echo "$r" | grep -q '"percentage"' && pass "GET /syncs/progress/{doc}" || fail "GET /syncs/progress/{doc}"
fi

echo
[ "$FAIL" -eq 0 ] && echo "Phase 5: all checks passed" || echo "Phase 5: one or more checks FAILED"
exit "$FAIL"
