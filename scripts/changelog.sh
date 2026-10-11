#!/usr/bin/env bash
# Prepend the next release's section to CHANGELOG.md with git-cliff.
# Usage: scripts/changelog.sh [TAG]   (TAG, e.g. v1.0.0, overrides the computed version)
#
# Refuses (exit 1, file untouched) when no commit since the last v* tag is releasable. git-cliff
# renders a "### <group>" heading for every group that has at least one commit left after the
# skip rules, so a dry render with no "### " line means there is nothing to release.
set -euo pipefail

if [ "$#" -gt 1 ]; then
  echo "usage: $0 [TAG]" >&2
  exit 2
fi
tag="${1:-}"

cd "$(git rev-parse --show-toplevel)"

if [ -n "$tag" ]; then
  version_args=(--tag "$tag")
else
  version_args=(--bump)
fi

preview="$(git-cliff --unreleased "${version_args[@]}")"
if ! grep -q '^### ' <<<"$preview"; then
  last_tag="$(git describe --tags --abbrev=0 --match 'v[0-9]*' 2>/dev/null || true)"
  echo "nothing to release since ${last_tag:-the first commit}" >&2
  exit 1
fi

git-cliff --unreleased "${version_args[@]}" --prepend CHANGELOG.md
if [ -n "$tag" ]; then
  echo "$tag"
else
  git-cliff --unreleased --bumped-version
fi
