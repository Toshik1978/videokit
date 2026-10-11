#!/usr/bin/env bash
# Print the CHANGELOG.md section for a release tag (the release workflow's notes).
# Usage: extract-changelog.sh <tag> [changelog]   (default changelog: CHANGELOG.md)
#
# A section starts at the heading "## <tag>" or "## <tag> ..." and ends before the next "## ".
# The heading is matched by literal prefix, not regex: dots in the tag are not wildcards, and
# v1.5.0 never matches v1.50.0.
# Exit 1: no section, or only whitespace in it. Exit 2: usage error or missing file.
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

section="$(awk -v tag="$tag" '
  $0 == "## " tag || index($0, "## " tag " ") == 1 { found = 1; print; next }
  found && index($0, "## ") == 1 { exit }
  found { print }
' "$changelog")"

# The heading alone is not a section: the body after it must have content.
body="$(printf '%s\n' "$section" | tail -n +2)"
if [ -z "$(printf '%s' "$body" | tr -d '[:space:]')" ]; then
  echo "error: no '## $tag' section found in $changelog" >&2
  echo "hint: write the release notes (task changelog) before tagging" >&2
  exit 1
fi

printf '%s\n' "$section"
