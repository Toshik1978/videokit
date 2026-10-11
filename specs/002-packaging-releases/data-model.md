# Data Model: Slim Image and Release Flow

This feature adds no application data. Its "entities" are release artifacts; their rules are what
the scripts and the release workflow enforce.

## Release

| Field | Rule |
| --- | --- |
| `tag` | `vMAJOR.MINOR.PATCH`, digits only (regex `^v[0-9]+\.[0-9]+\.[0-9]+$`); any other tag does not start a release |
| `commit` | the tagged commit; MUST be an ancestor of (or equal to) `origin/main` |
| `notes` | the `CHANGELOG.md` section headed `## <tag> — <date>`; MUST be the newest section and MUST contain non-whitespace text |
| `image_tags` | `X.Y.Z` (tag without `v`) and `latest` |
| `attestation` | build provenance for the pushed image digest, stored in the registry |
| `release_page` | named `<tag>`; body = `notes` + a `docker pull ghcr.io/<owner>/<repo>:X.Y.Z` block |

**Lifecycle**: tag pushed → guards (format, on main, newest changelog entry, notes present) →
checks (everything CI runs) → image rebuilt from cache, smoke test + size guard → image pushed
(`X.Y.Z`, `latest`) → attestation → release page. Any failure before "image pushed" publishes
nothing; a failure after it is retried by deleting and re-pushing the tag (RELEASING.md), which
overwrites the image tags.

## Changelog (`CHANGELOG.md`)

| Part | Rule |
| --- | --- |
| header | fixed preamble, byte-identical to `.cliff.toml`'s `header` (git-cliff `--prepend` relies on it) |
| section | `## vX.Y.Z — YYYY-MM-DD`, a hand-written summary paragraph, then generated groups `### Features`, `### Bug Fixes`, `### Others`, then `---` |
| order | newest section first |
| excluded commits | merges, `chore`, `test`, `style`, `build(deps…)`, `ci(deps…)` (so `chore(release): vX.Y.Z` never appears) |
| next version | computed from commits since the last `v*` tag: `feat` → minor, `fix`/other → patch, breaking → minor while `0.x` (`breaking_always_bump_major = false`); no tag yet → `v0.1.0`; `TAG=` overrides |

## Image

| Property | Rule |
| --- | --- |
| platform | `linux/amd64` only |
| compressed size | `docker save | gzip -6` byte count ≤ 1 250 000 000 (`scripts/image_size_guard.sh`) |
| GPU libraries | only those the transcription engine loads (cuBLAS/cuBLASLt for CUDA 12, pinned in `uv.lock`), registered with `ldconfig` |
| GPU env | `NVIDIA_VISIBLE_DEVICES=all`, `NVIDIA_DRIVER_CAPABILITIES=compute,utility` |
| user | default uid 10001; MUST also run under any `--user uid:gid` |
| writes | only under `/data` (`DATA_DIR`, `HF_HOME`, `XDG_CACHE_HOME`); `/app` read-only |
| labels | `org.opencontainers.image.title=VideoKit`, `.description`, `.authors=Anton Krivenko`, `.licenses=MIT`, `.source`, `.version` (the last two set by the release) |
