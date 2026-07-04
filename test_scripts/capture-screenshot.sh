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

echo "Device : $HOST -> $DEVICE_IP"
echo "Output : $OUT_DIR/sample_screenshot.png"
echo "         $OUT_DIR/sample_ocr.txt"
echo

docker build -t "$IMAGE" "$REPO_ROOT" > /dev/null 2>&1 \
  && echo "Image ready." \
  || { echo "docker build failed"; exit 1; }

docker run --init --rm --network host $ADD_HOST \
  -v "$OUT_DIR:/output" \
  "$IMAGE" python -m xteink_service.capture "$HOST"
