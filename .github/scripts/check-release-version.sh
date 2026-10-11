#!/usr/bin/env bash
# Fail unless the release tag is the newest version CHANGELOG.md documents.
# Usage: check-release-version.sh <tag> [changelog]   (default changelog: CHANGELOG.md)
#
# Tagging an older or mistyped version fails here instead of publishing an unexpected release.
# Exit 1: tag differs from the newest "## " heading. Exit 2: usage error, missing file or no heading.
set -euo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ] || [ -z "$1" ]; then
  echo "usage: $(basename "$0") <tag> [changelog]" >&2
  exit 2
fi
tag="$1"
changelog="${2:-CHANGELOG.md}"

if [ ! -f "$changelog" ]; then
  echo "error: $changelog not found" >&2
  exit 2
fi

# The newest heading is the first one: "## v1.0.0 — 2026-08-01"; its second field is the version.
latest="$(awk '/^## /{print $2; exit}' "$changelog")"
if [ -z "$latest" ]; then
  echo "error: no '## ' release heading found in $changelog" >&2
  exit 2
fi

if [ "$tag" != "$latest" ]; then
  echo "error: tag '$tag' does not match the newest changelog version '$latest'" >&2
  echo "  tag:       $tag" >&2
  echo "  changelog: $latest ($changelog)" >&2
  echo "hint: run task changelog for $tag, or tag the version the changelog documents" >&2
  exit 1
fi

echo "release version: $latest (tag $tag)"
