#!/usr/bin/env bash
# Recover a previously-captured sample screenshot into the real vault.
# Use this when the device SD card is lost but test_scripts/sample_screenshot.png
# was saved from an earlier capture-screenshot.sh run.
#
# Usage:
#   ./test_scripts/recover-screenshot.sh "Book Title" YYYY-MM-DD [index]
#
# Arguments:
#   "Book Title"  — exact book name as shown by capture-screenshot.sh ("Book : ...")
#   YYYY-MM-DD    — date shown by capture-screenshot.sh ("Day: ...")
#   index         — screenshot number within that day (default: 1)
#
# Example:
#   ./test_scripts/recover-screenshot.sh "Pastoral" 2026-07-04
#   ./test_scripts/recover-screenshot.sh "Pastoral" 2026-07-04 2

set -euo pipefail

BOOK="${1:-}"
DAY="${2:-}"
INDEX="${3:-1}"
IMAGE="xteink-service:dev"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SCRIPTS_DIR="$REPO_ROOT/test_scripts"

if [[ -z "$BOOK" || -z "$DAY" ]]; then
  echo "Usage: $0 \"Book Title\" YYYY-MM-DD [index]"
  echo "  Book Title and date were printed by capture-screenshot.sh."
  exit 1
fi

if [[ ! -f "$SCRIPTS_DIR/sample_screenshot.png" ]]; then
  echo "Error: $SCRIPTS_DIR/sample_screenshot.png not found."
  exit 1
fi

echo "Book  : $BOOK"
echo "Day   : $DAY"
echo "Index : $INDEX"
echo

docker build -t "$IMAGE" "$REPO_ROOT" > /dev/null 2>&1 \
  && echo "Docker image built." \
  || { echo "docker build failed"; exit 1; }

docker run --init --rm \
  -v "$SCRIPTS_DIR:/input:ro" \
  -v "/srv/docker/data/syncthing/shares/xteink-vault:/data/vault" \
  "$IMAGE" python - <<PYEOF
import sys
from datetime import date
from pathlib import Path
from xteink_service.vault_writer import VaultWriter

book  = """$BOOK"""
day   = date.fromisoformat("$DAY")
index = $INDEX

png  = Path("/input/sample_screenshot.png").read_bytes()
ocr_path = Path("/input/sample_ocr.txt")
ocr  = ocr_path.read_text().strip() if ocr_path.exists() else None

vw = VaultWriter("/data/vault")
embed = vw.write_screenshot(book, day, png, index)
vw.append_to_daily_note(book, day, embed, ocr)
print(f"Written : Books/{embed}")
if ocr:
    print(f"OCR     : {len(ocr)} chars appended to Books/{book}.md")
else:
    print("OCR     : none (sample_ocr.txt missing or empty)")
PYEOF
