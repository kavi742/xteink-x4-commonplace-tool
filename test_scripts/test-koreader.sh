#!/usr/bin/env bash
# Interactive KOReader sync server test.
# Starts the server, runs curl tests, shows the DB, tears down.
# Usage: bash test_scripts/test-koreader.sh

set -uo pipefail
FAIL=0
IMAGE="xteink-service:dev"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT=8090
CONTAINER=""

pass() { echo "  ok   - $1"; }
fail() { echo "  FAIL - $1"; FAIL=1; }

cleanup() {
  [ -n "$CONTAINER" ] && docker rm -f "$CONTAINER" > /dev/null 2>&1
}
trap cleanup EXIT

echo "== build =="
if docker build -t "$IMAGE" "$REPO_ROOT" > /dev/null 2>&1; then
  pass "image built"
else
  fail "docker build failed"; exit 1
fi

echo "== start server =="
CONTAINER=$(docker run -d \
  -p "$PORT:$PORT" \
  -e "KOREADER_DB=/tmp/koreader_test.db" \
  "$IMAGE" \
  python -m uvicorn xteink_service.koreader_sync:app \
    --host 0.0.0.0 --port "$PORT" --log-level warning)
echo "  container: ${CONTAINER:0:12}"

# Wait for server to be ready
for i in $(seq 1 10); do
  sleep 1
  curl -sf "http://localhost:$PORT/users/auth" > /dev/null 2>&1 && break
  [ "$i" -eq 10 ] && { fail "server did not start"; exit 1; }
done
pass "server ready at localhost:$PORT"

BASE="http://localhost:$PORT"

echo "== auth =="
r=$(curl -sf "$BASE/users/auth")
echo "$r" | grep -q '"OK"' && pass "GET /users/auth" || fail "GET /users/auth"

r=$(curl -sf -X POST "$BASE/users/create")
echo "$r" | grep -q '"OK"' && pass "POST /users/create" || fail "POST /users/create"

echo "== progress sync =="
r=$(curl -sf -X PUT "$BASE/syncs/progress" \
  -H "Content-Type: application/json" \
  -d '{"document":"Pastoral.epub","progress":"0/Chapter8","percentage":22.5,"device":"xteink-x4","device_id":"test001"}')
echo "  PUT  -> $r"
echo "$r" | grep -q '"Pastoral.epub"' && pass "PUT /syncs/progress" || fail "PUT /syncs/progress"

r=$(curl -sf -X POST "$BASE/syncs/progress" \
  -H "Content-Type: application/json" \
  -d '{"document":"Pastoral.epub","progress":"0/Chapter10","percentage":35.0,"device":"xteink-x4","device_id":"test001"}')
echo "  POST -> $r"
echo "$r" | grep -q '"percentage": *35' && pass "POST /syncs/progress (update)" || fail "POST /syncs/progress (update)"

r=$(curl -sf "$BASE/syncs/progress/Pastoral.epub")
echo "  GET  -> $r"
echo "$r" | grep -q '"percentage": *35' && pass "GET /syncs/progress/{doc} returns latest" || fail "GET /syncs/progress/{doc} returns latest"

r=$(curl -sf "$BASE/syncs/progress/nonexistent.epub")
echo "  GET nonexistent -> $r"
[ "$r" = "{}" ] && pass "GET /syncs/progress/{doc} unknown returns {}" || fail "GET /syncs/progress/{doc} unknown"

echo "== list all =="
r=$(curl -sf "$BASE/syncs/progress")
count=$(echo "$r" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo 0)
echo "  $count record(s) stored"
[ "$count" -ge 2 ] && pass "GET /syncs/progress lists $count records" || fail "expected >= 2 records, got $count"

echo "== second book =="
curl -sf -X PUT "$BASE/syncs/progress" \
  -H "Content-Type: application/json" \
  -d '{"document":"Another Book.epub","progress":"0/Ch1","percentage":5.0}' > /dev/null
r=$(curl -sf "$BASE/syncs/progress")
count=$(echo "$r" | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo 0)
[ "$count" -ge 3 ] && pass "multiple books stored ($count records)" || fail "multiple books"

echo
[ "$FAIL" -eq 0 ] && echo "KOReader sync: all checks passed" || echo "KOReader sync: one or more checks FAILED"
exit "$FAIL"
