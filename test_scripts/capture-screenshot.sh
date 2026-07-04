#!/usr/bin/env bash
# Capture a real screenshot from the X4, save PNG + OCR text to test_scripts/.
# Usage: ./test_scripts/capture-screenshot.sh [host]
# Output: test_scripts/sample_screenshot.png  and  test_scripts/sample_ocr.txt

set -uo pipefail
HOST="${1:-crosspoint.local}"
IMAGE="xteink-service:dev"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OUT_DIR="$REPO_ROOT/test_scripts"

# Resolve hostname on the host.
if [[ "$HOST" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  DEVICE_IP="$HOST"
else
  DEVICE_IP=$(avahi-resolve -n "$HOST" 2>/dev/null | awk '{print $2}')
  [ -z "$DEVICE_IP" ] && DEVICE_IP="$HOST"
fi
ADD_HOST="--add-host $HOST:$DEVICE_IP"

echo "Device : $HOST → $DEVICE_IP"
echo "Output : $OUT_DIR/sample_screenshot.png"
echo "         $OUT_DIR/sample_ocr.txt"
echo

docker run --init --rm --network host $ADD_HOST \
  -e "_HOST=$HOST" \
  -v "$OUT_DIR:/output" \
  "$IMAGE" python - <<'PYEOF'
import asyncio, os, sys
import aiohttp
from xteink_service.archiver import ScreenshotArchiver

HOST = os.getenv("_HOST", "crosspoint.local")

async def main():
    a = ScreenshotArchiver("/vault", HOST)
    async with aiohttp.ClientSession() as s:
        shots = await a._list_screenshots(s)
    if not shots:
        print("No screenshots found on device.")
        sys.exit(1)

    book, day, path = shots[0]
    print(f"Screenshot : {path}")
    print(f"Book       : {book}  |  Day: {day}")

    async with aiohttp.ClientSession() as s:
        bmp = await a._download_file(s, path)
    png = a._bmp_to_png(bmp)

    with open("/output/sample_screenshot.png", "wb") as f:
        f.write(png)

    text = a._ocr_image(png) or ""
    with open("/output/sample_ocr.txt", "w") as f:
        f.write(text)

    print(f"\n--- OCR output ---")
    print(text if text else "(empty — blank page or no recognisable text)")

asyncio.run(main())
PYEOF
