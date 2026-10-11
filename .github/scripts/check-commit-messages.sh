#!/usr/bin/env bash
#
# Check every commit message in a range against Conventional Commits.
#
# Usage: check-commit-messages.sh BASE [HEAD]
#
# Runs the repo's own commit-msg hook via pre-commit, so .pre-commit-config.yaml is the single
# definition of the rule: the same tool at the same pinned rev that contributors get locally from
# `git commit`. Used by .github/workflows/commit-lint.yml; also runnable locally:
#
#   .github/scripts/check-commit-messages.sh origin/main HEAD
#
# Merge commits are skipped (git generates their subjects). When BASE is empty, all zeros (the
# "before" of a branch's first push) or not a known commit, there is no range to walk and only the
# tip commit is checked.

set -euo pipefail

base="${1:-}"
head="${2:-HEAD}"

if [ -n "$base" ] && git rev-parse --verify --quiet "${base}^{commit}" >/dev/null; then
  revs=$(git rev-list --no-merges "${base}..${head}")
else
  revs=$(git rev-list --no-merges -1 "${head}")
fi

msg_file=$(mktemp)
trap 'rm -f "$msg_file"' EXIT

checked=0
failed=0
while IFS= read -r sha; do
  [ -n "$sha" ] || continue
  checked=$((checked + 1))
  git log -1 --format=%B "$sha" > "$msg_file"
  if ! pre-commit run conventional-pre-commit \
    --hook-stage commit-msg --commit-msg-filename "$msg_file"; then
    # pre-commit does not say which commit the message came from.
    echo "       in $(git rev-parse --short "$sha")" >&2
    failed=$((failed + 1))
  fi
done <<< "$revs"

echo "checked $checked commit message(s)"

if [ "$failed" -gt 0 ]; then
  echo "error: $failed commit message(s) do not follow Conventional Commits" >&2
  echo "hint: install the hooks locally so this fails at commit time:" >&2
  echo "      pre-commit install" >&2
  exit 1
fi
