#!/usr/bin/env bash
# Full pipeline test: wipe vault, sync screenshots, resolve aliases, send
# KOReader progress, verify vault output.
# Device must be reachable. Run from any directory.

set -uo pipefail
REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${1:-crosspoint.local}"
VAULT="$REPO/test_scripts/vault"
STATE_DB="$VAULT/state.db"
KOREADER_DB="$REPO/test_scripts/koreader.db"
PORT=8090
FAIL=0

pass() { echo "  ok   $1"; }
fail() { echo "  FAIL $1"; FAIL=1; }
section() { echo; echo "== $1 =="; }

# ── helpers ───────────────────────────────────────────────────────────────────

check_device() {
  if ! curl -sf --connect-timeout 3 "http://$HOST/api/status" >/dev/null 2>&1; then
    echo "ERROR: Cannot reach $HOST — put the X4 in File Transfer mode first."
    exit 1
  fi
}

kill_server() {
  local pid
  pid=$(lsof -ti ":$PORT" 2>/dev/null | head -1)
  [ -n "$pid" ] && kill "$pid" 2>/dev/null && sleep 1
}

# ── 0. pre-flight ─────────────────────────────────────────────────────────────

section "pre-flight"
check_device
pass "device reachable at $HOST"

kill_server

# ── 1. wipe vault ─────────────────────────────────────────────────────────────

section "wipe"
rm -rf "$VAULT" "$KOREADER_DB"
mkdir -p "$VAULT"
pass "vault + koreader.db wiped"

# ── 2. run tests ──────────────────────────────────────────────────────────────

section "unit tests"
cd "$REPO"
if uv run pytest -q 2>&1 | tail -3; then
  pass "pytest"
else
  fail "pytest"
fi

# ── 3. sync screenshots + resolve aliases ─────────────────────────────────────

section "sync_once (screenshots + OCR + alias scan)"
if KOREADER_DB="$KOREADER_DB" uv run python -m xteink_service.sync_once \
    "$HOST" "$VAULT" "$STATE_DB" 2>&1 | grep -E "Archived|Resolved|warn|error"; then
  :
fi

png_count=$(find "$VAULT/Books" -name "*.png" 2>/dev/null | wc -l)
json_count=$(find "$VAULT/Books" -name "*.json" 2>/dev/null | wc -l)
md_count=$(find "$VAULT/Books" -name "*.md" 2>/dev/null | wc -l)

[ "$png_count" -gt 0 ] && pass "$png_count PNG(s) archived" || fail "no PNGs written"
[ "$json_count" -eq "$png_count" ] && pass "$json_count JSON sidecar(s)" || fail "sidecars mismatch"
[ "$md_count" -gt 0 ] && pass "$md_count book note(s) created" || fail "no book notes"

# ── 4. show alias table ───────────────────────────────────────────────────────

section "alias table"
uv run python -m xteink_service.alias --state "$STATE_DB" --koreader "$KOREADER_DB"

# ── 5. start KOReader sync server ─────────────────────────────────────────────

section "koreader sync server"
VAULT_PATH="$VAULT" STATE_DB="$STATE_DB" KOREADER_DB="$KOREADER_DB" \
  DEVICE_HOST="$HOST" \
  uv run uvicorn xteink_service.koreader_sync:app \
    --host 0.0.0.0 --port "$PORT" --log-level info &
SERVER_PID=$!
sleep 2

if curl -sf "http://localhost:$PORT/health" >/dev/null; then
  pass "server healthy on :$PORT (pid $SERVER_PID)"
else
  fail "server did not start"
  exit 1
fi

# ── 6. send mock KOReader syncs for each resolved alias ───────────────────────

section "mock KOReader syncs"

while IFS='|' read -r hash title; do
  [ -z "$hash" ] && continue
  response=$(curl -sf -X PUT "http://localhost:$PORT/syncs/progress" \
    -H "Content-Type: application/json" \
    -d "{\"document\":\"$hash\",\"progress\":\"/body/DocFragment[5]/body\",\"percentage\":0.25}" \
    -w "\n%{http_code}")
  code=$(echo "$response" | tail -1)
  if [ "$code" = "200" ]; then
    pass "PUT /syncs/progress → $title ($code)"
  else
    fail "PUT /syncs/progress → $title (got $code)"
  fi
  sleep 0.5
done < <(uv run python -c "
import sqlite3, os
try:
    conn = sqlite3.connect('$STATE_DB')
    for row in conn.execute('SELECT hash, title FROM document_aliases'):
        print(f'{row[0]}|{row[1]}')
except Exception:
    pass
")

sleep 1

# ── 7. verify vault output ────────────────────────────────────────────────────

section "vault output"

if [ -f "$VAULT/Reading Log.md" ]; then
  pass "Reading Log.md exists"
  echo "  --- Reading Log.md ---"
  cat "$VAULT/Reading Log.md"
else
  fail "Reading Log.md missing"
fi

echo
echo "  --- vault tree ---"
find "$VAULT" -not -path "*/.obsidian/*" -type f | sort | while read -r f; do
  echo "  ${f#$VAULT/}"
done

# ── 8. cleanup ────────────────────────────────────────────────────────────────

section "cleanup"
kill "$SERVER_PID" 2>/dev/null && pass "server stopped"

echo
[ "$FAIL" -eq 0 ] && echo "Full pipeline: all checks passed" \
                  || echo "Full pipeline: one or more checks FAILED"
exit "$FAIL"
