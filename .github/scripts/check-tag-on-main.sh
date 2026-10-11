#!/usr/bin/env bash
# Fail unless a commit is on the main branch (so only reviewed code is released).
# Usage: check-tag-on-main.sh <sha> [branch-ref]   (default branch ref: origin/main)
# Exit 1: the commit is not an ancestor of the branch. Exit 2: usage error.
set -euo pipefail

if [ "$#" -lt 1 ] || [ "$#" -gt 2 ] || [ -z "$1" ]; then
  echo "usage: $(basename "$0") <sha> [branch-ref]" >&2
  exit 2
fi
sha="$1"
branch="${2:-origin/main}"

if git merge-base --is-ancestor "$sha" "$branch" 2>/dev/null; then
  echo "$sha is on main"
else
  echo "$sha is not on main" >&2
  exit 1
fi
