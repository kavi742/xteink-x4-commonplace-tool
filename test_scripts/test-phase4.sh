#!/usr/bin/env bash
# Phase 4 test: screenshot archiver + vault writer
# Run from anywhere: bash test_scripts/test-phase4.sh [host]
# Pass host to enable live end-to-end test (device must be in File Transfer mode).

set -uo pipefail
FAIL=0
HOST="${1:-}"
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

echo "== unit tests: archiver =="
if docker run --rm "$IMAGE" python -m pytest tests/test_archiver.py -v; then
  pass "pytest test_archiver"
else
  fail "pytest test_archiver"
fi

echo "== unit tests: vault writer =="
if docker run --rm "$IMAGE" python -m pytest tests/test_vault_writer.py -v; then
  pass "pytest test_vault_writer"
else
  fail "pytest test_vault_writer"
fi

if [ -z "$HOST" ]; then
  echo
  echo "Tip: pass a host to run the live end-to-end test:"
  echo "  ./test-phase4.sh crosspoint.local"
else
  # Resolve hostname.
  if [[ "$HOST" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    DEVICE_IP="$HOST"; ADD_HOST=""
  else
    DEVICE_IP=$(getent hosts "$HOST" 2>/dev/null | awk '{print $1}')
    if [ -z "$DEVICE_IP" ]; then
      echo "  warn - could not resolve $HOST; using hostname directly"
      DEVICE_IP="$HOST"; ADD_HOST=""
    else
      ADD_HOST="--add-host $HOST:$DEVICE_IP"
    fi
  fi

  VAULT_DIR=$(mktemp -d)
  echo "== live end-to-end sync (device: $HOST -> $DEVICE_IP) =="
  echo "  Vault dir: $VAULT_DIR"

  docker run --init --rm --network host $ADD_HOST \
    --user "$(id -u):$(id -g)" \
    -v "$VAULT_DIR:/vault" \
    "$IMAGE" python -m xteink_service.sync_once "$HOST" /vault

  echo "  --- vault contents ---"
  find "$VAULT_DIR" -type f | sort

  if find "$VAULT_DIR" -name "*.md" | grep -q .; then
    pass "daily note(s) created in vault"
  else
    fail "no markdown files written to vault"
  fi

  if find "$VAULT_DIR" -name "*.png" | grep -q .; then
    pass "PNG file(s) written to attachments"
  else
    fail "no PNG files written to vault"
  fi

  # Show the first daily note so we can inspect the OCR callout
  first_note=$(find "$VAULT_DIR" -name "*.md" | sort | head -1)
  if [ -n "$first_note" ]; then
    echo "  --- first daily note ---"
    cat "$first_note"
  fi

  rm -rf "$VAULT_DIR"
fi

echo
[ "$FAIL" -eq 0 ] && echo "Phase 4: all checks passed" || echo "Phase 4: one or more checks FAILED"
exit "$FAIL"
