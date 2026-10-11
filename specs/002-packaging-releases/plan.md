# Implementation Plan: Slim Image and Release Flow

**Branch**: `002-packaging-releases` | **Date**: 2026-10-11 | **Spec**: [spec.md](spec.md)

**Input**: Feature specification from `specs/002-packaging-releases/spec.md`

## Summary

Replace the `nvidia/cuda` cuDNN runtime base with `python:3.14-slim` plus the one GPU library the
transcription engine actually loads (cuBLAS for CUDA 12, a pinned pip dependency registered with
`ldconfig`), Debian's ffmpeg and a cache-free dependency layer: ≈ 1.06 GB compressed instead of
3.8 GB, same single image for CPU and GPU hosts, guarded by a 1.25 GB size check in CI and
releases and by a few-second local GPU check. Add the author's release flow (git-cliff changelog
with automatic version, `v*` tag → release workflow that reruns all of CI, checks tag/changelog/
main, then pushes the image to ghcr.io as `X.Y.Z` + `latest` with provenance and creates the
release page), make the tag the only version, switch `compose.yaml` to the published image with
GPU on, and add CONTRIBUTING, RELEASING, the README "About" section, the author's name and the
"VideoKit" display name. Experiments behind the numbers are in [research.md](research.md).

## Technical Context

**Language/Version**: Python 3.14 (backend, scripts), TypeScript 6 / Vue 3 (frontend), Bash
(release and Docker scripts), YAML (workflows, compose), TOML (git-cliff)

**Primary Dependencies**: Docker BuildKit; `python:3.14-slim`; `nvidia-cublas-cu12` (new backend
dependency); Debian `ffmpeg`; uv; git-cliff (new mise tool); GitHub Actions (`docker/login-action`,
`docker/setup-buildx-action`, `docker/metadata-action`, `docker/build-push-action`,
`actions/attest`, `softprops/action-gh-release`); actionlint (new mise tool, part of `task lint`);
`pyyaml` + `types-pyyaml` in the backend dev group (YAML config tests)

**Storage**: N/A (no application data changes); `./.data` bind mount unchanged

**Testing**: pytest for scripts (Bash via `subprocess`, like `test_check_commit_messages.py`;
`gpu_check.py` with fake modules), Vitest/Playwright for the renamed title and manifest, the
Docker smoke test (extended with a codec check) and the size guard in CI; the GPU check runs
manually on a GPU host

**Target Platform**: Linux amd64 Docker hosts, with or without an NVIDIA GPU + NVIDIA container
toolkit; GitHub Actions `ubuntu-latest`; ghcr.io

**Project Type**: web service packaged as one container + repository release tooling

**Performance Goals**: compressed image ≤ 1.25 GB (≈ 1.06 GB measured); local GPU check < 10 s
with a cached model (2.1 s measured); tag push → published release < 30 min

**Constraints**: nothing published unless every check passes; the release automation never
writes to the repository; no personal or environment data except the author's name; amd64 only

**Scale/Scope**: one maintainer, one release line, one image

## Constitution Check

*GATE: Must pass before Phase 0 research. Re-check after Phase 1 design.*

| Principle | Status | Notes |
| --- | --- | --- |
| I. Open-Source Hygiene | ⚠ amend | Putting the author's name in `LICENSE`/metadata and the owner handle in the repository's own addresses conflicts with "MUST NOT contain … usernames". The feature amends Principle I (v1.2.0) to allow the author's name in authorship places and the repository's public addresses (`github.com/toshik1978/videokit`, `ghcr.io/toshik1978/videokit`) in links and configuration; everything else stays forbidden. |
| II. Simplicity First | ✅ | Still one container; no new infrastructure. New tooling (git-cliff, release workflow) is the author's standard set, justified by FR-010–FR-015. One image instead of CPU/GPU variants. |
| III. Multi-Tool Modularity | ✅ | No application code changes beyond removing `__version__`. |
| IV. Provider Abstraction / GPU optional | ✅ | The image still runs on CPU-only hosts; only the example compose file enables the GPU by default, with documented removal. |
| V. Test-First | ✅ | Every script gets failing tests first; CI stays GPU-free (the GPU check is manual by design, FR-009). |
| VI. Privacy & Data Lifecycle | ✅ | Unchanged. |
| VII. Security Boundary | ✅ | Unchanged; README keeps the no-auth warning. |
| Tech constraints: "Packaging: one multi-stage Dockerfile; an example compose.yaml with optional GPU reservation" | ⚠ amend | Still one multi-stage Dockerfile; compose now enables the GPU by default. The amendment rewrites this line to describe the slim single image and the compose default. |
| Workflow: "Every change goes through a pull request" | ✅ | Releases add no direct-to-main automation; the workflow is read-only on the repository. |

Both ⚠ items are resolved by the constitution amendment in this feature (MINOR bump, recorded in
the constitution's sync impact report), not by exceptions. Post-design re-check: unchanged.

## Project Structure

### Documentation (this feature)

```text
specs/002-packaging-releases/
├── plan.md
├── research.md
├── data-model.md
├── quickstart.md
├── contracts/
│   ├── commands.md
│   ├── image.md
│   └── release-workflow.md
├── checklists/requirements.md
└── tasks.md             # /speckit-tasks
```

### Source Code (repository root)

```text
Dockerfile                          # rewritten runtime stage (slim base, cuBLAS via pip, ldconfig, labels)
compose.yaml                        # published image, GPU on
Taskfile.yml                        # changelog, release:verify, docker:size, docker:gpu-check
.mise.toml                          # + git-cliff, actionlint
.cliff.toml                         # new
CHANGELOG.md                        # new (header only until the first release)
LICENSE                             # author name
README.md                           # VideoKit, About this project, quickstart with the published image
CONTRIBUTING.md                     # new
docs/RELEASING.md                   # new
.claude/CLAUDE.md                   # rule + display name + commands
.specify/memory/constitution.md     # v1.2.0
.github/
├── dependabot.yml                  # ignore python image minor/major bumps
├── scripts/
│   ├── check-commit-messages.sh    # unchanged
│   ├── extract-changelog.sh        # new
│   ├── check-release-version.sh    # new
│   └── check-tag-on-main.sh        # new
└── workflows/
    ├── ci.yml                      # + workflow_call, + size guard in image job
    └── release.yml                 # new
scripts/
├── docker_smoke.sh                 # + codec check
├── image_size_guard.sh             # new
└── gpu_check.py                    # new
backend/
├── pyproject.toml                  # version 0.0.0 + comment, authors, nvidia-cublas-cu12
├── uv.lock
├── src/videokit/__init__.py        # __version__ removed
└── tests/unit/                     # test_package.py updated; new tests for the scripts
frontend/
├── package.json                    # no version, author
├── index.html                      # VideoKit
├── public/manifest.webmanifest     # VideoKit
├── src/i18n/en.json                # app.title VideoKit
└── tests/unit/manifest.spec.ts     # expects VideoKit
```

**Structure Decision**: no new top-level projects. Release scripts sit next to the existing
`.github/scripts/check-commit-messages.sh`; Docker scripts next to `scripts/docker_smoke.sh`;
script tests live in `backend/tests/unit/` (mypy and ruff already cover `../scripts`).

## Phases (for /speckit-tasks)

1. **Slim image** (US1): Dockerfile rewrite + `nvidia-cublas-cu12` dependency; smoke test codec
   check; size guard script + CI image job + Taskfile; GPU check script + Taskfile; Dependabot
   python-image ignore; verify on the GPU host.
2. **Version and identity** (US4 part, prerequisite for labels and docs): remove hardcoded
   versions; author in LICENSE and metadata; VideoKit display name in UI, manifest and tests;
   constitution v1.2.0 and CLAUDE.md rule.
3. **Release flow** (US2): git-cliff config + CHANGELOG header + `task changelog`; release-note
   scripts; tag-on-main script; `release:verify`; `ci.yml` `workflow_call`; `release.yml`;
   `docs/RELEASING.md`.
4. **Published image and docs** (US3 + rest of US4): `compose.yaml`; README (About, quickstart with
   the published image, GPU section); CONTRIBUTING.md.

## Repository address

- The repository is `github.com/toshik1978/videokit` and the image `ghcr.io/toshik1978/videokit`
  (confirmed by the author). `compose.yaml`, the README and the changelog commit links use them;
  the release workflow derives the image name from `github.repository`.

## Complexity Tracking

No unjustified violations: the two constitution conflicts are resolved by amendment (see
Constitution Check).
