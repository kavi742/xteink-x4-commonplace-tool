#!/usr/bin/env bash
# Phase 4 test: screenshot archiver (list, download, BMP→PNG conversion)
# Run from anywhere: bash test_scripts/test-phase4.sh
#
# Automated unit tests only — live tests require the device and will be
# added when end-to-end sync is wired together in a later step.

set -uo pipefail
FAIL=0
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
if docker run --rm "$IMAGE" python -m pytest tests/test_archiver.py -v; then
  pass "pytest"
else
  fail "pytest"
fi

echo
[ "$FAIL" -eq 0 ] && echo "Phase 4 (so far): all checks passed" || echo "Phase 4: one or more checks FAILED"
exit "$FAIL"
