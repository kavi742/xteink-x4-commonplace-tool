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
  ADD_HOST=""
else
  DEVICE_IP=$(getent hosts "$HOST" 2>/dev/null | awk '{print $1}')
  if [ -z "$DEVICE_IP" ]; then
    echo "  warn - could not resolve $HOST; using hostname directly via --network host"
    DEVICE_IP="$HOST"
    ADD_HOST=""
  else
    ADD_HOST="--add-host $HOST:$DEVICE_IP"
  fi
fi

echo "Device : $HOST -> $DEVICE_IP"
echo "Output : $OUT_DIR/sample_screenshot.png"
echo "         $OUT_DIR/sample_ocr.txt"
echo

docker build -t "$IMAGE" "$REPO_ROOT" > /dev/null 2>&1 \
  && echo "Docker image built." \
  || { echo "docker build failed"; exit 1; }

docker run --init --rm --network host $ADD_HOST \
  -v "$OUT_DIR:/output" \
  "$IMAGE" python -m xteink_service.capture "$HOST"
