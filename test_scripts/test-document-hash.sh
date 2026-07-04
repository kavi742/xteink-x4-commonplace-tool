#!/usr/bin/env bash
# Verify CrossPoint document hash algorithm against real epub files on the device.
# Usage: bash test_scripts/test-document-hash.sh <host> [expected_hash ...]
#
# Puts the device in File Transfer mode, browses for epub files,
# downloads each one, computes the CrossPoint partial-MD5 hash,
# and marks any that match the provided expected hashes.
#
# Example:
#   bash test_scripts/test-document-hash.sh crosspoint.local \
#     c370c1faafe89878f69442274df5f37f \
#     17fbaaeb12a635cabc236ec6062491b7

set -uo pipefail
HOST="${1:-crosspoint.local}"
shift || true
KNOWN_HASHES="$*"
IMAGE="xteink-service:dev"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

# Resolve hostname on host
if [[ "$HOST" =~ ^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$ ]]; then
  DEVICE_IP="$HOST"; ADD_HOST=""
else
  DEVICE_IP=$(getent hosts "$HOST" 2>/dev/null | awk '{print $1}')
  if [ -z "$DEVICE_IP" ]; then
    echo "warn - could not resolve $HOST; using hostname directly"
    DEVICE_IP="$HOST"; ADD_HOST=""
  else
    ADD_HOST="--add-host $HOST:$DEVICE_IP"
  fi
fi

echo "Device : $HOST -> $DEVICE_IP"
echo "Known  : ${KNOWN_HASHES:-none}"
echo

docker build -t "$IMAGE" "$REPO_ROOT" > /dev/null 2>&1 \
  && echo "Docker image built." \
  || { echo "docker build failed"; exit 1; }

docker run --init --rm --network host $ADD_HOST "$IMAGE" \
  python -m xteink_service.hash_books "$HOST" $KNOWN_HASHES
