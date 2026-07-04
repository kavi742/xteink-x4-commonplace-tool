#!/usr/bin/env bash
# KOReader sync server test.
# Usage:
#   bash test_scripts/test-koreader.sh          # automated curl tests only
#   bash test_scripts/test-koreader.sh --live   # + wait for real X4 sync

set -uo pipefail
FAIL=0
LIVE=false
[[ "${1:-}" == "--live" ]] && LIVE=true
IMAGE="xteink-service:dev"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PORT=8090
CONTAINER=""
LIVE_CONTAINER=""

pass() { echo "  ok   - $1"; }
fail() { echo "  FAIL - $1"; FAIL=1; }

cleanup() {
  [ -n "$CONTAINER" ]      && docker rm -f "$CONTAINER"      > /dev/null 2>&1
  [ -n "$LIVE_CONTAINER" ] && docker rm -f "$LIVE_CONTAINER" > /dev/null 2>&1
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

# ------------------------------------------------------------------ #
# Live hardware test — only with --live flag                          #
# ------------------------------------------------------------------ #
if $LIVE; then
  echo
  echo "== live hardware test =="
  SERVER_IP=$(hostname -I | awk '{print $1}')

  LIVE_CONTAINER=$(docker run -d \
    -p "$PORT:$PORT" \
    "$IMAGE" \
    python -m uvicorn xteink_service.koreader_sync:app \
      --host 0.0.0.0 --port "$PORT" --log-level warning)

  for i in $(seq 1 10); do
    sleep 1
    curl -sf "http://localhost:$PORT/users/auth" > /dev/null 2>&1 && break
  done
  pass "live server ready at $SERVER_IP:$PORT"

  echo
  echo "  Configure X4 KOReader Sync:"
  echo "    Server   : http://$SERVER_IP:$PORT"
  echo "    Username : xteink"
  echo "    Password : sync"
  echo "    Doc match: Filename"
  echo "    Metadata : enabled"
  echo
  echo "  Open any book on the X4. Waiting up to 60s for a sync..."

  BEFORE=$(curl -sf "http://localhost:$PORT/syncs/progress" \
    | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo 0)

  RECEIVED=false
  for i in $(seq 1 30); do
    sleep 2
    printf "."
    AFTER=$(curl -sf "http://localhost:$PORT/syncs/progress" \
      | python3 -c "import sys,json; print(len(json.load(sys.stdin)))" 2>/dev/null || echo 0)
    if [ "$AFTER" -gt "$BEFORE" ]; then
      RECEIVED=true
      echo
      echo "  Sync received!"
      curl -sf "http://localhost:$PORT/syncs/progress" | python3 -m json.tool
      pass "X4 KOReader sync received ($AFTER record(s))"
      break
    fi
  done

  if ! $RECEIVED; then
    echo
    fail "No sync received within 60s — is the X4 configured and a book open?"
  fi
fi

exit "$FAIL"
