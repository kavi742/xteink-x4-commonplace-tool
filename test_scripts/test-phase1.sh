#!/usr/bin/env bash
# Smoke test for Phase 1: vault structure, .stignore, .gitignore, and the
# Docker setup. Run from the repo root: ./scripts/test-phase1.sh
#
# Fast checks always run. Docker checks run only if `docker` is present
# (graceful degradation, per AGENT.md) and are skipped with a note otherwise.

set -uo pipefail
FAIL=0
VAULT_DIR="./vault_smoketest"

check() {
  if eval "$2"; then
    echo "  ok   - $1"
  else
    echo "  FAIL - $1"
    FAIL=1
  fi
}

echo "== vault structure =="
# Resolve init-vault.sh next to this script, not relative to the caller's
# CWD — makes this work regardless of what directory you run it from.
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
INIT_SCRIPT="$SCRIPT_DIR/init-vault.sh"
if [ ! -f "$INIT_SCRIPT" ]; then
  echo "  FAIL - init-vault.sh not found at $INIT_SCRIPT (expected next to this test script)"
  FAIL=1
else
rm -rf "$VAULT_DIR"
"$INIT_SCRIPT" "$VAULT_DIR" > /dev/null
check "Reading Log/ exists"   "[ -d '$VAULT_DIR/Reading Log' ]"
check "Books/ exists"         "[ -d '$VAULT_DIR/Books' ]"
check ".stignore exists"      "[ -f '$VAULT_DIR/.stignore' ]"
check ".stignore ignores Obsidian workspace" "grep -q 'workspace' '$VAULT_DIR/.stignore'"
# idempotency: running twice should not error or duplicate
"$INIT_SCRIPT" "$VAULT_DIR" > /dev/null
check "init-vault.sh is idempotent (second run exits 0)" "[ $? -eq 0 ]"
rm -rf "$VAULT_DIR"
fi

echo "== .gitignore =="
check "vault/ is git-ignored"  "git check-ignore -q vault/dummy 2>/dev/null || git check-ignore -q vault"
check "state/ is git-ignored"  "git check-ignore -q state/dummy 2>/dev/null || git check-ignore -q state"
check ".env is git-ignored"    "git check-ignore -q .env"
check "uv.lock is NOT ignored" "! git check-ignore -q uv.lock"

echo "== docker =="
if command -v docker > /dev/null 2>&1; then
  check "docker-compose.yml is valid config" "docker compose config > /dev/null 2>&1"
  check "Dockerfile builds"                  "docker build -t xteink-service:smoketest . > /tmp/xteink-build.log 2>&1"
  if [ -f /tmp/xteink-build.log ] && docker image inspect xteink-service:smoketest > /dev/null 2>&1; then
    check "tesseract-ocr is installed in the image" \
      "docker run --rm xteink-service:smoketest tesseract --version > /dev/null 2>&1"
    check "all Python deps import in the image" \
      "docker run --rm xteink-service:smoketest python -c 'import fastapi, aiohttp, websockets, PIL, pytesseract, aiosqlite' 2>/dev/null"
  fi
else
  echo "  skip - docker not installed, skipping build/compose checks"
fi

echo
if [ "$FAIL" -eq 0 ]; then
  echo "Phase 1: all checks passed"
else
  echo "Phase 1: one or more checks FAILED (see above)"
fi
exit "$FAIL"
