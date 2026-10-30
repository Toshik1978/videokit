#!/usr/bin/env bash
# Fail when an image's compressed size (`docker save | gzip -6`) exceeds the limit.
# Usage: scripts/image_size_guard.sh <image>
set -euo pipefail

LIMIT_BYTES=330000000

if [ "$#" -ne 1 ] || [ -z "$1" ]; then
  echo "usage: $0 <image>" >&2
  exit 2
fi
image="$1"

if ! size="$(docker save "$image" | gzip -6 | wc -c)"; then
  echo "error: could not measure image '$image' (docker save failed)" >&2
  exit 2
fi
size="${size//[[:space:]]/}"

gb() { awk -v b="$1" 'BEGIN { printf "%.2f", b / 1000000000 }'; }

if [ "$size" -gt "$LIMIT_BYTES" ]; then
  echo "image size: $size bytes ($(gb "$size") GB) exceeds the limit of $LIMIT_BYTES bytes ($(gb "$LIMIT_BYTES") GB)" >&2
  exit 1
fi
echo "image size: $(gb "$size") GB (limit $(gb "$LIMIT_BYTES") GB)"
