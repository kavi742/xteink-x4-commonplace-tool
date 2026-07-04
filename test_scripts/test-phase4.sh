#!/usr/bin/env bash
# Phase 4 test: screenshot archiver (list, download, BMP→PNG conversion, OCR)
# Run from anywhere: bash test_scripts/test-phase4.sh [host]
# Pass host to enable live OCR test (device must be in File Transfer mode).

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

echo "== unit tests =="
if docker run --rm "$IMAGE" python -m pytest tests/test_archiver.py -v; then
  pass "pytest"
else
  fail "pytest"
fi

if [ -z "$HOST" ]; then
  echo
  echo "Tip: pass a host to run live OCR test, e.g.:"
  echo "  ./test-phase4.sh crosspoint.local"
  echo "  ./test-phase4.sh 192.168.x.x"
else
  # Resolve hostname on the host so the container can reach it.
  if [[ "$HOST" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
    DEVICE_IP="$HOST"
  else
    DEVICE_IP=$(avahi-resolve -n "$HOST" 2>/dev/null | awk '{print $2}')
    [ -z "$DEVICE_IP" ] && DEVICE_IP="$HOST"
  fi
  ADD_HOST="--add-host $HOST:$DEVICE_IP"

  echo "== live OCR test (device: $HOST → $DEVICE_IP) =="
  echo "  Fetching first available screenshot and running Tesseract..."
  docker run --init --rm --network host $ADD_HOST -e "_OCR_HOST=$HOST" "$IMAGE" python - <<'PYEOF'
import asyncio, sys
import aiohttp
from xteink_service.archiver import ScreenshotArchiver
import os

HOST = os.environ.get("_OCR_HOST", "crosspoint.local")

async def main():
    a = ScreenshotArchiver("/vault", HOST)
    async with aiohttp.ClientSession() as s:
        shots = await a._list_screenshots(s)
    if not shots:
        print("No screenshots found on device.")
        sys.exit(0)
    book, day, path = shots[0]
    print(f"Screenshot : {path}")
    print(f"Book       : {book}  |  Day: {day}")
    async with aiohttp.ClientSession() as s:
        bmp = await a._download_file(s, path)
    png = a._bmp_to_png(bmp)
    text = a._ocr_image(png)
    print(f"\n--- OCR output ---")
    print(text if text else "(empty — blank page or Tesseract found no text)")

asyncio.run(main())
PYEOF
fi

echo
[ "$FAIL" -eq 0 ] && echo "Phase 4 (so far): all checks passed" || echo "Phase 4: one or more checks FAILED"
exit "$FAIL"
