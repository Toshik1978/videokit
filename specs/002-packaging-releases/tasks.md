---

description: "Task list for the slim image and release flow"
---

# Tasks: Slim Image and Release Flow

**Input**: Design documents from `specs/002-packaging-releases/`

**Prerequisites**: plan.md, spec.md, research.md, data-model.md, contracts/, quickstart.md

**Tests**: Every task is implemented test-first (Constitution V): each task writes its own failing
tests in the listed test file(s) before the change; configuration- or docs-only parts are verified
by the listed commands. There are no standalone test tasks. Default tests must pass without API
keys, network access to AI providers, or a GPU, and keep line coverage ≥ 80 % per stack.

**Organization**: Four phases, as in plan.md: slim image (US1), version and identity (US4),
release flow (US2), published image and docs (US3, US4), then polish. Each task is one checklist
line (what + main files) followed by indented detail bullets that are part of the task.

## Format: `[ID] [P?] [Story] Description`

- **[P]**: Can run in parallel (different files, no dependencies on incomplete tasks)
- **[Story]**: User story the task belongs to (US1–US4 from spec.md)
- Script tests live in `backend/tests/unit/` (ruff and mypy already cover `../scripts`); Bash
  scripts are exercised through `subprocess`, like `test_check_commit_messages.py`

---

## Phase 1: Slim image (User Story 1, Priority: P1) 🎯 MVP

**Goal**: one image for CPU and GPU hosts at ≤ 1.25 GB compressed, guarded by CI and checkable on
a GPU host in seconds.

**Independent Test**: quickstart.md sections 1 and 2.

- [x] T001 [US1] Rebuild the runtime stage on `python:3.14-slim` with cuBLAS from pip in `Dockerfile`, `backend/pyproject.toml`, `backend/uv.lock`
  - Backend dependency `nvidia-cublas-cu12` (no version cap beyond the lock; comment: "GPU matrix math for ctranslate2 (CUDA 12); the only GPU library it loads — see specs/002-packaging-releases/research.md R2"). Do NOT add `nvidia-cudnn-cu12`.
  - Runtime stage: `FROM python:3.14-slim`; `apt-get install -y --no-install-recommends ca-certificates ffmpeg` then `rm -rf /var/lib/apt/lists/*` (keep the `hadolint ignore=DL3008` comment); `COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /usr/local/bin/uv`; env `UV_PYTHON_DOWNLOADS=never`, `UV_PYTHON=/usr/local/bin/python3`, `UV_PROJECT_ENVIRONMENT=/app/.venv`, `UV_COMPILE_BYTECODE=1`, `UV_LINK_MODE=copy`; both `uv sync` steps use `RUN --mount=type=cache,target=/root/.cache/uv`; no `uv python install`, no `/opt/python`.
  - After the dependency sync: `find /app/.venv -path "*/nvidia/*/lib" -type d > /etc/ld.so.conf.d/nvidia.conf && ldconfig`.
  - `ENV NVIDIA_VISIBLE_DEVICES=all NVIDIA_DRIVER_CAPABILITIES=compute,utility`.
  - Labels: `org.opencontainers.image.title="VideoKit"`, `org.opencontainers.image.description` (one sentence: self-hosted video tools, YouTube Prep first), `org.opencontainers.image.authors="Anton Krivenko"`, `org.opencontainers.image.licenses="MIT"`.
  - Keep everything else from feature 001: frontend build stage, user 10001 with `/data` owned by it, `chmod -R a+rX /app`, `HF_HOME=/data/huggingface` and `XDG_CACHE_HOME=/data/cache` set after the build steps, `VOLUME ["/data"]`, `EXPOSE 8000`, HEALTHCHECK, CMD; update the stage comments (single image for CPU and GPU hosts; the host driver comes from the NVIDIA container toolkit).
  - Tests: `backend/tests/unit/test_dockerfile.py` — the runtime `FROM` is `python:<X.Y>-slim` with `<X.Y>` equal to `backend/.python-version`; no `nvidia/cuda` anywhere; no `uv python install`; both `uv sync` lines use the uv cache mount; the `ldconfig` line and both NVIDIA env vars exist; the four labels exist; `backend/pyproject.toml` lists `nvidia-cublas-cu12` and no `nvidia-cudnn`.
  - Verify: `task docker:build`; `scripts/docker_smoke.sh videokit:local` passes; `docker run --rm --entrypoint sh videokit:local -c 'ldconfig -p | grep -c libcublasLt.so.12; ls /usr/local/cuda'` prints ≥ 1 and "No such file"; `task lint` (hadolint) passes.
- [x] T002 [P] [US1] Add the compressed-size guard `scripts/image_size_guard.sh`, task `docker:size` in `Taskfile.yml` and a size step in the CI `image` job in `.github/workflows/ci.yml`
  - Usage `scripts/image_size_guard.sh <image>`; measures `docker save "$image" | gzip -6 | wc -c`; the limit is the single constant `LIMIT_BYTES=1250000000` in this script.
  - Success: prints `image size: <GB with 2 decimals> GB (limit 1.25 GB)`, exit 0. Over the limit: prints the measured size and the limit to stderr, exit 1. Missing argument or `docker save` failure: usage/error line to stderr, exit 2.
  - `task docker:size` runs it with `IMAGE` defaulting to `videokit:local`; the CI `image` job runs `scripts/image_size_guard.sh videokit:ci` after the smoke test.
  - Tests: `backend/tests/unit/test_image_size_guard.py` — a fake `docker` on `PATH` that writes N bytes for `save`: under, exactly at and over the limit; missing argument → 2; failing `docker save` → 2.
- [x] T003 [P] [US1] Add a codec check to the container smoke test in `scripts/docker_smoke.sh`
  - Before the health checks, run one `docker run --rm --entrypoint python "$image"` that, in a temp dir, generates 2-second test clips with the image's own ffmpeg — H.264/AAC (`.mp4`), HEVC/AAC (`.mp4`), VP9/Opus (`.webm`), AV1/Opus (`.webm`, encoder `libsvtav1` or `libaom-av1`, whichever the image has), H.264/MP3 (`.mkv`) — and runs each through the app's media code (`videokit.core.media.probe`, then audio and frame extraction as `MediaService.extract` does) (FR-006).
  - Prints `codecs ok` on success; on failure names the clip (e.g. `codec check failed: av1-opus.webm`) and exits non-zero, failing the smoke test.
  - Verify: `scripts/docker_smoke.sh videokit:local` prints `codecs ok` and `smoke test passed`; temporarily breaking one encoder name makes it fail naming that clip.
- [x] T004 [US1] Add the local GPU check `scripts/gpu_check.py` and task `docker:gpu-check` in `Taskfile.yml`
  - Runs inside the image (no check code ships in the product). Steps: `ctranslate2.get_cuda_device_count()` = 0 → stderr `no GPU visible to the container`, exit 1; generate 5 s of audio with `ffmpeg -f lavfi -i sine=frequency=440:duration=5 -ar 16000 -ac 1` into a temp dir; `faster_whisper.WhisperModel("tiny", device="cuda", compute_type="float16")` (load failure → `GPU libraries failed to load: <exception type>`, exit 1); the loaded model must report `model.model.device == "cuda"`, else stderr `model is not on the GPU (device: <device>)`, exit 1; transcribe and consume the segments (failure → `transcription on the GPU failed: <exception type>`, exit 1); success → stdout `GPU check passed: <n> GPU(s), model load <s>s, transcription <s>s`, exit 0. Never print a traceback or exception message, only the type.
  - `task docker:gpu-check`: `mkdir -p .data`, then `docker run --rm --gpus all --user "$(id -u):$(id -g)" -v "$PWD/.data:/data" -v "$PWD/scripts/gpu_check.py:/gpu_check.py:ro" --entrypoint python videokit:local /gpu_check.py`; the model is cached under `./.data/huggingface`.
  - Tests: `backend/tests/unit/test_gpu_check.py` — fake `ctranslate2` and `faster_whisper` modules in `sys.modules` and a stubbed ffmpeg call: no GPU, model load error, model reporting `cpu`, transcription error, success message format; asserts no traceback text in output.
  - Verify on a GPU host: two runs of `task docker:gpu-check`; the second passes in under 10 s (SC-002); without `--gpus` the script prints the no-GPU line.
- [x] T005 [P] [US1] Stop Dependabot from proposing Python image minor/major upgrades in `.github/dependabot.yml`
  - In the `docker` ecosystem entry: `ignore: - dependency-name: "python"` with `update-types: ["version-update:semver-major", "version-update:semver-minor"]`, and a comment that Python minors move together in `.mise.toml`, `backend/.python-version`, `requires-python` and the Dockerfile after checking wheel availability.
  - Add `pyyaml` and `types-pyyaml` to the backend dev group (explicit for config tests).
  - Tests: `backend/tests/unit/test_dependabot.py` — the docker entry ignores those two update types for `python`; other ecosystems unchanged.
- [x] T006 [US1] Record the image rules in `.claude/CLAUDE.md`
  - Gotchas: the Python version's four places are now `.mise.toml`, `backend/.python-version`, `requires-python` and the Dockerfile's `python:X.Y-slim` tag; the image ships only cuBLAS (ctranslate2 loads nothing else — research R2), so run `task docker:gpu-check` after ctranslate2/faster-whisper upgrades; the 1.25 GB limit lives in `scripts/image_size_guard.sh`; replace the "CUDA base image must stay on 12.x" note with "the GPU library package must stay `-cu12` while ctranslate2 links `libcublas.so.12`".
  - Commands block: `task docker:size`, `task docker:gpu-check`.
  - Verify: `grep -n "gpu-check\|image_size_guard\|python:3.14-slim" .claude/CLAUDE.md`.

**Checkpoint**: quickstart.md sections 1–2 pass; `task lint && task test` green.

---

## Phase 2: Version and identity (User Story 4, Priority: P3; prerequisite for labels and docs)

**Goal**: the tag is the only version, the author is named, and the product is called VideoKit.

**Independent Test**: quickstart.md section 5 greps; UI title and manifest read "VideoKit".

- [x] T007 [P] [US4] Remove hardcoded versions in `backend/pyproject.toml`, `backend/src/videokit/__init__.py`, `backend/tests/unit/test_package.py`, `frontend/package.json`, `frontend/package-lock.json`
  - `backend/pyproject.toml`: `version = "0.0.0"` with the comment `# The git tag is the version (docs/RELEASING.md); the build backend requires this field.`
  - `videokit/__init__.py`: remove `__version__` and `__all__` entry; `test_package.py`: drop `test_version_is_exported`, keep the import test.
  - `frontend/package.json`: remove `"version"` (it stays `"private": true`); regenerate `package-lock.json` with `npm install` so its root `version` fields go too.
  - Tests: `backend/tests/unit/test_versioning.py` — `videokit` has no `__version__`; pyproject version is `"0.0.0"` and the comment line precedes it; `frontend/package.json` has no `version` key.
- [x] T008 [US4] Name the author in `LICENSE`, `backend/pyproject.toml`, `frontend/package.json`
  - `LICENSE`: `Copyright (c) 2026 Anton Krivenko`.
  - `pyproject.toml`: `authors = [{ name = "Anton Krivenko" }]` (no email). `package.json`: `"author": "Anton Krivenko"`.
  - Tests: `backend/tests/unit/test_authorship.py` — LICENSE line, pyproject `authors`, package.json `author`, and the Dockerfile `org.opencontainers.image.authors` label all say exactly `Anton Krivenko`; none contains `@`.
- [x] T009 [P] [US4] Use the display name VideoKit in `frontend/src/i18n/en.json`, `frontend/index.html`, `frontend/public/manifest.webmanifest`
  - `en.json` `app.title`, `index.html` `<title>` and `<meta name="apple-mobile-web-app-title">`, manifest `name` and `short_name`: `VideoKit`.
  - Keep lowercase identifiers (storage key `videokit.uploads`, package names).
  - Tests: update `frontend/tests/unit/manifest.spec.ts` to expect `VideoKit` for `name` and `short_name`; add an assertion (unit or e2e `frontend/tests/e2e/flow.spec.ts`) that the page title is `VideoKit`.
- [x] T010 [US4] Amend the constitution to v1.2.0 and mirror it in `.specify/memory/constitution.md` and `.claude/CLAUDE.md`
  - Principle I: keep every prohibition, change "usernames" to "usernames other than the author's", and add: "The author's name, Anton Krivenko, MAY appear where authorship is stated: LICENSE, package metadata, image labels and documentation bylines. The repository's own public addresses (`github.com/toshik1978/videokit`, `ghcr.io/toshik1978/videokit`) MAY appear in links and configuration. Nothing else about the author (email, other accounts, hosts, hardware) may appear."
  - Technology & Operational Constraints, Packaging line: "one multi-stage Dockerfile building a single image for CPU and GPU hosts on a slim Python base that carries only the GPU libraries the transcription engine loads; an example `compose.yaml` that runs the published image with GPU access enabled by default."
  - Title `# VideoKit Constitution` and prose "VideoKit"; `**Version**: 1.2.0`, `**Last Amended**: 2026-10-11`; a Sync Impact Report HTML comment at the top (1.1.0 → 1.2.0, MINOR, sections changed, templates checked).
  - `.claude/CLAUDE.md`: heading `# VideoKit — guide for Claude`, prose uses VideoKit, the public-repo non-negotiable gains the author-name and repository-address exceptions with the same limits.
  - Verify: `grep -n "Anton Krivenko\|1.2.0" .specify/memory/constitution.md .claude/CLAUDE.md`; `task lint` passes (denylist hook does not list the author's name).

**Checkpoint**: `task lint && task test` green; quickstart.md section 5 greps pass (except docs from Phase 4).

---

## Phase 3: Release flow (User Story 2, Priority: P1)

**Goal**: `task changelog` → commit → tag publishes a checked image and release page; any failure
publishes nothing.

**Independent Test**: quickstart.md section 3 in a scratch clone; `actionlint` passes; the first
real release (section 4) is the author's act.

- [x] T011 [US2] Add git-cliff changelog generation in `.mise.toml`, `.cliff.toml`, `CHANGELOG.md`, `scripts/changelog.sh`, `Taskfile.yml`
  - `.mise.toml`: `git-cliff = "latest"`.
  - `.cliff.toml` adapted from the author's firefly-jar: header (title `# Changelog`, one paragraph "What changed in each VideoKit release and why. Summaries are written by hand; the commit lists under them are generated with git-cliff via `task changelog`.", the semver and Conventional Commits line, `---`), body `## {{ version }} — {{ timestamp | date(format="%Y-%m-%d") }}` with groups and commit links `https://github.com/toshik1978/videokit/commit/{{ commit.id }}`, `[git]` `conventional_commits = true`, `filter_unconventional = false`, `tag_pattern = "v[0-9]*"`, `sort_commits = "oldest"`, commit_parsers in order: skip `^Merge `, `^chore`, `^test`, `^style`, `^build\(deps`, `^ci\(deps`; then `^feat` → `<!-- 0 -->Features`, `^fix` → `<!-- 1 -->Bug Fixes`, `.*` → `<!-- 2 -->Others`; `[bump]` `initial_tag = "v0.1.0"`, `breaking_always_bump_major = false`.
  - `CHANGELOG.md`: exactly the header (byte-identical to `.cliff.toml`'s `header`, which `--prepend` relies on).
  - `scripts/changelog.sh [TAG]`: refuses with `nothing to release since <last tag or "the first commit">` (exit 1, file unchanged) when git-cliff finds no releasable commits; otherwise runs `git-cliff --unreleased --bump --prepend CHANGELOG.md`, or `--tag "$TAG"` instead of `--bump` when given, and prints the new version. `task changelog` calls it with `{{.TAG}}`.
  - Tests: `backend/tests/unit/test_changelog.py` (skipped with a reason if `git-cliff` is not on `PATH`; CI has it via mise) — in temp git repos with the real `.cliff.toml` and `CHANGELOG.md`: no tags → `## v0.1.0`; after tag `v0.1.0` + `feat` → `v0.2.0`; `fix` only → `v0.1.1`; `feat!` while 0.x → `v0.2.0`; `TAG=v1.0.0` override; `chore(release): v0.1.0`, `test`, `style`, `build(deps)`, `ci(deps)` and merge commits never listed; header preserved once at the top; no releasable commits → exit 1, file unchanged.
- [x] T012 [US2] Add the release guard scripts `.github/scripts/extract-changelog.sh`, `.github/scripts/check-release-version.sh`, `.github/scripts/check-tag-on-main.sh` and task `release:verify` in `Taskfile.yml`
  - `extract-changelog.sh <tag> [changelog]`: prints the section whose heading is `## <tag>` or starts with `## <tag> ` (literal prefix match, not regex), up to the next `## `; missing or whitespace-only section → stderr `error: no '## <tag>' section found in <file>` + hint, exit 1; usage or missing file → exit 2.
  - `check-release-version.sh <tag> [changelog]`: the newest `## ` heading's version must equal the tag → stdout `release version: <tag> (tag <tag>)`; otherwise stderr names both, exit 1; no heading, usage or missing file → exit 2.
  - `check-tag-on-main.sh <sha> [branch-ref]` (default `origin/main`): `git merge-base --is-ancestor` → `<sha> is on main`, else stderr `<sha> is not on main`, exit 1.
  - `task release:verify TAG=vX.Y.Z` (requires `TAG`): runs `check-release-version.sh` then `extract-changelog.sh` (output discarded).
  - Tests: `backend/tests/unit/test_release_scripts.py` — section extraction (first, middle, last section; `v1.5.0` does not match `v1.50.0`; empty section), version match/mismatch/no heading, tag on main / on a side branch (temp git repos), exit codes per contract.
- [x] T013 [US2] Add the release workflow `.github/workflows/release.yml`, make CI callable in `.github/workflows/ci.yml`, and lint workflows with actionlint in `.mise.toml` and `Taskfile.yml`
  - `ci.yml`: add `workflow_call:` to `on:`; nothing else changes (badge and PR-comment steps already condition on `main` pushes and pull requests).
  - `release.yml` per `contracts/release-workflow.md`: `on: push: tags: ['v[0-9]+.[0-9]+.[0-9]+']`; `concurrency: release-${{ github.ref }}`, `cancel-in-progress: false`; top-level `permissions: contents: read`.
  - Job `checks`: checkout with `fetch-depth: 0`; fail unless `$GITHUB_REF_NAME` matches `^v[0-9]+\.[0-9]+\.[0-9]+$`; `check-tag-on-main.sh "$GITHUB_SHA" origin/main`; `check-release-version.sh "$GITHUB_REF_NAME"`; `extract-changelog.sh "$GITHUB_REF_NAME" > "$RUNNER_TEMP/release-notes.md"`, uploaded as an artifact.
  - Job `ci`: `uses: ./.github/workflows/ci.yml` with `permissions: contents: read, pull-requests: write`.
  - Job `publish` (`needs: [checks, ci]`; `permissions: contents: write, packages: write, id-token: write, attestations: write`): checkout; image name `ghcr.io/${GITHUB_REPOSITORY,,}`; `docker/setup-buildx-action`; `docker/login-action` (ghcr.io, `github.actor`, `GITHUB_TOKEN`); `docker/metadata-action` with `tags: type=semver,pattern={{version}}` and `type=raw,value=latest`, labels adding `org.opencontainers.image.title=VideoKit`; `docker/build-push-action` with `platforms: linux/amd64`, `load: true`, tag `videokit:release`, `cache-from/cache-to: type=gha`; `scripts/docker_smoke.sh videokit:release`; `scripts/image_size_guard.sh videokit:release`; `docker/build-push-action` again with `push: true`, the metadata tags and labels, same cache (id `push`); `actions/attest` (`subject-name` = image name, `subject-digest` = `steps.push.outputs.digest`, `push-to-registry: true`); download the notes, append a `### Docker` block with ```` ```bash ```` `docker pull <image>:X.Y.Z` ```` ``` ````; `softprops/action-gh-release` with `tag_name`, `name: ${{ github.ref_name }}`, `body_path`.
  - All actions pinned to moving major tags (`@vN`), latest majors at implementation time.
  - `.mise.toml`: `actionlint = "latest"`; `Taskfile.yml`: `lint:workflows` (`actionlint`) included in `lint`.
  - Tests: `backend/tests/unit/test_release_workflow.py` (PyYAML) — trigger is only the tag pattern; job graph `publish` needs `checks` and `ci`; `ci` uses `./.github/workflows/ci.yml`; smoke and size steps come before the push step, attestation and release after it; only `publish` has write permissions; `ci.yml` has `workflow_call`. Verify: `task lint` (actionlint) passes.
- [x] T014 [US2] Write the release guide `docs/RELEASING.md`
  - Modeled on the author's firefly-jar RELEASING.md, adapted to VideoKit: intro (tagging and pushing are the author's act; never force-push a shared branch); "How versions work" (the tag is the only version; `pyproject.toml` holds `0.0.0`; the image gets `X.Y.Z` and `latest`; versions follow semver and git-cliff computes the next one, `TAG=` overrides, e.g. for `v1.0.0`); Step 0 — before the first push only: sweep the whole history (including `.beads/issues.jsonl`) for personal data with `git log -p --all | grep -nE '<pattern>'`, then create the repository and add the `GIST_ID`/`GIST_TOKEN` secrets; Step 1 `task changelog` and writing the summary paragraph (what the release means for someone running VideoKit, anything they must do); Step 2 preview with `.github/scripts/extract-changelog.sh vX.Y.Z`; Step 3 `task release:verify TAG=vX.Y.Z`, `task lint && task test`, commit `chore(release): vX.Y.Z`, push `main`; Step 4 `git tag -a vX.Y.Z -m vX.Y.Z && git push origin vX.Y.Z`; what the workflow does (checks, CI, publish; nothing published on failure); after the first release: make the ghcr.io package public (package settings → Change visibility); retrying a failed release (fix on `main`, `gh release delete vX.Y.Z` if a page exists, delete the tag locally and remotely, re-tag and push; never force-push a tag).
  - Verify: every command in the guide exists (`task --list`, script paths); `task lint` passes.

**Checkpoint**: quickstart.md section 3 passes in a scratch clone; `task lint && task test` green.

---

## Phase 4: Published image and docs (User Story 3, Priority: P2; User Story 4)

**Goal**: `docker compose up` runs the published image with the GPU; the repository explains what
VideoKit is and that it is shared as is.

**Independent Test**: quickstart.md sections 4 (after the first release) and 5.

- [x] T015 [US3] Run the published image with GPU access by default in `compose.yaml`
  - `image: ghcr.io/toshik1978/videokit:latest`; remove `build:`; keep `env_file`, port 8000, `./.data:/data`, `user: "${PUID:-1000}:${PGID:-1000}"`, `restart: unless-stopped` and their comments.
  - Uncomment the GPU block (`deploy.resources.reservations.devices`: `driver: nvidia`, `count: all`, `capabilities: [gpu]`) with the comment: needs the NVIDIA container toolkit; delete this block on hosts without it (then local transcription runs on the CPU).
  - Tests: `backend/tests/unit/test_compose.py` (PyYAML) — image is `ghcr.io/toshik1978/videokit:latest`, no `build`, the nvidia device reservation exists, user and volume unchanged.
  - Verify: `docker compose config` succeeds.
- [x] T016 [P] [US4] Update `README.md` for VideoKit, the published image and the project's status
  - H1 `# VideoKit`; prose uses VideoKit (commands, image and package names stay lowercase).
  - New section `## About this project` near the top: VideoKit is a large experiment in a fully AI-built application, taken from idea to running software by combining [Spec Kit](https://github.com/github/spec-kit) and [Superpowers](https://github.com/obra/superpowers); the code, tests and docs were written by AI agents under that workflow; it is shared as is — no issues, no pull requests, no support (see CONTRIBUTING.md).
  - Quickstart: `cp .env.example .env` (fill keys), `mkdir -p .data`, `docker compose up -d` pulls `ghcr.io/toshik1978/videokit:latest`; on hosts without the NVIDIA container toolkit delete the GPU block in `compose.yaml` first.
  - GPU section: the image includes the GPU library local transcription needs and runs on CPU without a GPU; `task docker:gpu-check` verifies a GPU host.
  - Development section: local builds with `task docker:build` / `task docker:run`; releases → `docs/RELEASING.md`; contributing → `CONTRIBUTING.md`. License section names Anton Krivenko.
  - Keep the no-authentication warning prominent (Constitution VII). Verify: `grep -c "videokit is\|# videokit" README.md` → 0.
- [x] T017 [P] [US4] Write `CONTRIBUTING.md` for fork users
  - Modeled on the author's firefly-jar CONTRIBUTING.md: title `# Working on VideoKit`; first paragraph: pull requests and issues are not accepted (link README "About this project"); the guide is for forks and local checkouts; `.claude/CLAUDE.md` holds the full rules for agents and wins where they differ.
  - Sections: Prerequisites (mise for the pinned toolchain, Task, pre-commit, Docker; optional NVIDIA container toolkit); Getting started (`mise install && task setup`, `pre-commit install`, `task lint`, `task test`, `task e2e`); The gate (what `task lint`/`task test` run, CI adds browser tests, image build + smoke + size guard and secret scan; never `--no-verify`); Conventions (Conventional Commits — the subject becomes the release note; Spec Kit + Superpowers, tests first; no new direct dependency without the author's approval); Never commit personal data (public repo; the denylist hook; placeholders); Releases (the author's act; `docs/RELEASING.md`).
  - Verify: every command exists (`task --list`); links resolve.

**Checkpoint**: quickstart.md section 5 passes; `docker compose config` OK.

---

## Phase 5: Polish & Cross-Cutting Concerns

- [x] T018 Sync `.claude/CLAUDE.md` with the finished feature
  - Commands: `task changelog`, `task release:verify TAG=…`, `task docker:size`, `task docker:gpu-check`, `actionlint` in `task lint`.
  - Layout: `.cliff.toml`, `CHANGELOG.md`, `CONTRIBUTING.md`, `docs/RELEASING.md`, `.github/scripts/*`, `.github/workflows/release.yml`, `scripts/image_size_guard.sh`, `scripts/gpu_check.py`.
  - Workflow: releases are tag-driven (`docs/RELEASING.md`); the release workflow reruns all of CI through `workflow_call`, so CI jobs must stay callable from a tag push.
  - Verify: `grep -n "RELEASING\|release.yml\|changelog" .claude/CLAUDE.md`.
- [x] T019 Validate the feature with `specs/002-packaging-releases/quickstart.md` sections 1, 2, 3 and 5
  - Run each section; fix anything that deviates from its expected output. Section 4 (first real release) is the author's act after merge.
  - Verify: `task lint && task test && task e2e` green; `gitleaks detect --redact` finds nothing.

---

## Dependencies & Execution Order

- **Phase 1** first: T001 before T002–T005 (they test or describe the new image); T004 after T002
  (both edit `Taskfile.yml`); T006 last in the phase.
- **Phase 2** after T001 (T008's test reads the Dockerfile label): T007 and T009 in parallel; T008
  after T007 (both edit `pyproject.toml` and `package.json`); T010 after T008.
- **Phase 3** after Phase 1 (the release reuses the smoke test and size guard): T011 → T012 → T013
  (all edit `Taskfile.yml`/`.mise.toml`) → T014.
- **Phase 4** after Phases 2–3 (docs describe the release flow and the author): T015, T016 and
  T017 touch different files; T016 and T017 in parallel after T015.
- **Phase 5** last.

## Parallel Example: Phase 1

```text
After T001:  T002 (size guard)  |  T003 (codec check)  |  T005 (Dependabot)
Then:        T004 (GPU check; shares Taskfile.yml with T002)
```

## Implementation Strategy

1. **MVP = Phase 1**: the slim image alone fixes the size problem and is usable locally
   (`task docker:build`, `task docker:run GPU=true`).
2. Phase 2 makes identity and versioning consistent before anything is published.
3. Phase 3 makes publishing possible; Phase 4 points users and compose at the published image.
4. The first real release (quickstart section 4) follows `docs/RELEASING.md` after the merge.

---

## Phase 6: Convergence

- [x] T020 List every prerequisite a fresh fork needs to reach a green gate and a working pre-push hook in `CONTRIBUTING.md` per FR-020, US4/AC2, SC-006 (partial)
  - Prerequisites: add ffmpeg (backend tests shell out to it; `backend/tests/conftest.py`) and beads `br` ≥ 1.9.0 (the `beads-setup` pre-push hook, `tools/beads/check-setup.sh`, refuses pushes without it).
  - Getting started: add `git config merge.beads.driver "br merge-driver %O %A %B"` after `pre-commit install`.
  - Verify: every command cited exists; the versions match `tools/beads/check-setup.sh`.
- [x] T021 Restrict `nvidia-cublas-cu12` to Linux in `backend/pyproject.toml` and `backend/uv.lock` per SC-006 (partial)
  - Marker `sys_platform == 'linux'` (the package ships Linux and Windows wheels only, so `uv sync` fails on macOS); relock; keep the comment.
  - Tests: extend `backend/tests/unit/test_dockerfile.py` (or the pyproject test) to assert the marker; the image still gets the package (amd64 Linux).
- [x] T022 Keep the transitive `nvidia-cuda-nvrtc-cu12` out of the image in `Dockerfile` per FR-002 (partial)
  - Both `uv sync` steps pass `--no-install-package nvidia-cuda-nvrtc-cu12` (the engine never loads it, research R2).
  - Tests: `backend/tests/unit/test_dockerfile.py` asserts the flag on both sync lines.
  - Verify: `task docker:build`, `scripts/docker_smoke.sh videokit:local`, `task docker:size`, and `task docker:gpu-check` on a GPU host.
- [x] T023 Stop shipping the uv binary in the runtime image in `Dockerfile` per FR-003 (partial)
  - Replace `COPY --from=ghcr.io/astral-sh/uv:0.13 /uv /usr/local/bin/uv` with a bind mount on each `uv sync` step (`--mount=from=ghcr.io/astral-sh/uv:0.13,source=/uv,target=/usr/local/bin/uv`), keeping the cache mount.
  - Tests: `backend/tests/unit/test_dockerfile.py` asserts no `COPY` of uv and the bind mount on both sync lines.
  - Verify: as T022; `docker run --rm --entrypoint python videokit:local -c "import shutil; print(shutil.which('uv'))"` prints `None`.
- [x] T024 Set the image description, licence and author labels explicitly in `.github/workflows/release.yml` per FR-013 (partial)
  - The metadata-action `labels:` input adds `org.opencontainers.image.description` (same sentence as the Dockerfile), `org.opencontainers.image.licenses=MIT` and `org.opencontainers.image.authors=Anton Krivenko` next to the title, so repository settings never override the Dockerfile values.
  - Tests: `backend/tests/unit/test_release_workflow.py` asserts the four labels; actionlint passes.
- [x] T025 Bring the release steps in `docs/RELEASING.md` to at most six required commands after the summary per SC-003 (partial)
  - Required path: `task release:verify TAG=…`, `git commit -m "chore(release): vX.Y.Z" CHANGELOG.md`, `git push`, `git tag -a …`, `git push origin vX.Y.Z`; the notes preview and `task lint && task test` are marked optional (the pre-push hook runs the gate).
  - Verify: count the required commands; every command exists.
- [x] T026 Fix quickstart section 3's last step in `specs/002-packaging-releases/quickstart.md` per quickstart §3 (contradicts)
  - Before the refusing `task changelog` run: commit `chore(release): v0.1.0` and tag `v0.1.0` in the scratch clone; expected `nothing to release since v0.1.0`, exit 1, file unchanged.
- [x] T027 Align `specs/002-packaging-releases/contracts/commands.md` with the implemented script outputs per contracts/commands.md (contradicts)
  - `scripts/gpu_check.py` row: add `could not generate test audio: <exception type>` (and the line T029 adds); `check-release-version.sh` row: success prints `release version: <tag> (tag <tag>)`; `task docker:run` row: `GPU=true` works as a task variable or an environment variable.
- [x] T028 Correct the stale lines in `README.md` per FR-017 (contradicts)
  - The provider table no longer calls CPU-only the default (compose enables the GPU); the mise comment lists git-cliff and actionlint; the `task lint` comment lists hadolint and actionlint; the specs pointer names `specs/002-packaging-releases/` too.
- [x] T029 Give model download/load failures their own reason in `scripts/gpu_check.py` per FR-009 (partial)
  - Errors raised by `WhisperModel` that are not GPU-library failures (e.g. a failed first-run download from Hugging Face) print `model could not be loaded: <exception type>`, exit 1; GPU library failures keep `GPU libraries failed to load: <exception type>` (decide by exception type and document the rule in the script).
  - Tests: `backend/tests/unit/test_gpu_check.py` covers both lines and the audio-generation failure line.
- [x] T030 Remove the default OpenAPI version and use the display name in `backend/src/videokit/main.py` per FR-016, FR-023 (unrequested)
  - `FastAPI(title="VideoKit", version="0.0.0")` or equivalent neutral value with a comment that the git tag is the version (FastAPI defaults to `0.1.0`, a fake release number).
  - Tests: a unit test reads `/api/openapi.json` (or `app.openapi()`) and asserts the title and that the version is not `0.1.0`.

## Phase 7: Convergence

- [x] T031 Report a GPU that is visible but unusable with its own reason in `scripts/gpu_check.py` per spec edge case "driver too old" (partial)
  - When `ctranslate2.get_cuda_device_count()` is 0 but an NVIDIA device is present in the container (`/dev/nvidia*` exists), print `GPU visible but CUDA unavailable (driver too old?)`, exit 1; otherwise keep `no GPU visible to the container`.
  - Tests: `backend/tests/unit/test_gpu_check.py` covers both branches (patch the device probe); `contracts/commands.md` lists the new line.
- [x] T032 Document what `task docker:gpu-check` does on a host without the NVIDIA container runtime in `README.md` and `specs/002-packaging-releases/quickstart.md` per FR-009 (partial)
  - Docker refuses `--gpus all` with its own error before the script runs; the script's `no GPU visible to the container` line appears when the image runs without `--gpus` (quickstart section 2's manual command).
- [x] T033 Move the optional release-notes preview before the required commands in `docs/RELEASING.md` per FR-015 (contradicts)
  - Order: changelog and summary, optional preview and checks, then the required commands, then tagging; add an "on `main`, up to date with the remote" reminder before the required commands.
- [x] T034 Reword the `TAG=v1.0.0` example in `docs/RELEASING.md` per FR-010, US2/AC2 (contradicts)
  - "for example `TAG=v1.0.0` when you decide to go 1.0"; the first release is the computed `v0.1.0`.
- [x] T035 Add a CPU transcription check of the built image to `specs/002-packaging-releases/quickstart.md` section 1 per US1/AC2 (partial)
  - A manual step (network for the model download is acceptable; CI stays network-free): run Whisper `tiny` with `device="cpu"` inside `videokit:local` as the host user with `./.data` mounted, e.g. a short `docker run … --entrypoint python videokit:local -c …` snippet that generates a sine with ffmpeg and transcribes it; expected: completes without error. Run it once and record the result.
- [x] T036 Fix the expected output of `check-release-version.sh` in `specs/002-packaging-releases/quickstart.md` section 3 per contracts/commands.md (contradicts)
  - `release version: v0.1.0 (tag v0.1.0)`.
- [x] T037 Merge the repeated `GPU libraries failed to load` entry in the `scripts/gpu_check.py` row of `specs/002-packaging-releases/contracts/commands.md` per contracts/commands.md (contradicts)
  - One entry with a parenthetical (library import, or a model-load `ImportError`/`OSError`/`RuntimeError` mentioning CUDA, cuBLAS or cuDNN); `model could not be loaded` stays separate; include T031's new line.
