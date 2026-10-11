#!/usr/bin/env python3
"""Block commits whose staged additions contain strings from a local, gitignored `.denylist`.

`.denylist` (repo root) holds one case-insensitive string per line; blank lines and lines
starting with `#` are ignored. Only added lines of `git diff --cached` are scanned. On a hit the
script exits 1 and reports `path:line` only; the matched string is never printed.
"""

from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

HUNK_RE = re.compile(r"^@@ -\d+(?:,(\d+))? \+(\d+)(?:,(\d+))? @@")


def _git(cwd: Path, *args: str) -> str:
    result = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return result.stdout


def load_terms(path: Path) -> list[str]:
    if not path.is_file():
        return []
    terms: list[str] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            terms.append(line.lower())
    return terms


def find_hits(diff: str, terms: list[str]) -> list[tuple[str, int]]:
    """Return (file, line number) for each added diff line containing a denied term."""
    hits: list[tuple[str, int]] = []
    current_file: str | None = None
    line_no = 0
    old_left = new_left = 0  # lines remaining in the current hunk; headers only occur outside one
    for line in diff.splitlines():
        if old_left > 0 or new_left > 0:
            if line.startswith("+"):
                new_left -= 1
                if current_file is not None and any(term in line[1:].lower() for term in terms):
                    hits.append((current_file, line_no))
                line_no += 1
            elif line.startswith("-"):
                old_left -= 1
            continue
        if line.startswith("+++ "):
            target = line[4:]
            current_file = None if target == "/dev/null" else target.removeprefix("b/")
        elif (match := HUNK_RE.match(line)) is not None:
            old_left = int(match.group(1) or 1)
            line_no = int(match.group(2))
            new_left = int(match.group(3) or 1)
    return hits


def main(cwd: Path | None = None) -> int:
    base = cwd if cwd is not None else Path.cwd()
    root = Path(_git(base, "rev-parse", "--show-toplevel").strip())
    terms = load_terms(root / ".denylist")
    if not terms:
        return 0
    diff = _git(root, "diff", "--cached", "--unified=0", "--no-color", "--no-ext-diff", "--diff-filter=ACMR")
    hits = find_hits(diff, terms)
    for file, line_no in hits:
        print(f"denylist match at {file}:{line_no}", file=sys.stderr)
    return 1 if hits else 0


if __name__ == "__main__":
    sys.exit(main())
