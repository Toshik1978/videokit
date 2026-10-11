# Research: Slim Image and Release Flow

All decisions below were settled in the brainstorming conversation or verified by throwaway
experiments on a GPU host (nothing from the experiments is kept). Measurements are amd64.

## R1. Where today's image size comes from

- **Finding**: the current image is 3.8 GB compressed (≈ 6.5 GB unpacked). The base
  `nvidia/cuda:*-cudnn-runtime` alone is 3.15 GB compressed: the CUDA toolkit libraries
  (`/usr/local/cuda`, 3.2 GB unpacked: cuBLAS, cuFFT, cuRAND, cuSOLVER, cuSPARSE, NPP, nvJPEG …),
  cuDNN (≈ 1 GB) and NCCL (0.4 GB). On top: Ubuntu's ffmpeg with its dependencies (0.47 GB), a
  uv-managed Python (0.12 GB), the virtual environment (0.5 GB) and a leftover uv download cache
  (0.45 GB) in the dependency layer.
- **Comparable images** (compressed, amd64): linuxserver faster-whisper 0.23 GB (CPU) / 1.66 GB
  (GPU, Ubuntu base + pip cuBLAS/cuDNN); speaches 0.85 / 2.91 GB; immich machine-learning
  0.27 / 2.76 GB; Jellyfin 0.75 GB; Ollama 3.78 GB.

## R2. Which GPU libraries the transcription engine needs

- **Decision**: ship only `nvidia-cublas-cu12` (cuBLAS + cuBLASLt for CUDA 12) as a pinned Python
  dependency. Do not ship cuDNN.
- **Evidence**: in a throwaway `python:3.14-slim` image with ctranslate2 4.8.2 and faster-whisper
  1.2.1, Whisper `tiny` and `large-v3-turbo` transcribed a real public-domain speech sample
  correctly on the GPU (`device="cuda"`, `compute_type="float16"`). `/proc/self/maps` after the
  run listed only `libcublas.so.12` and `libcublasLt.so.12`; with the cuDNN and nvrtc packages
  deleted the transcription still succeeded. ctranslate2's binary references `libcublas.so.12`
  only.
- **Rationale**: cuDNN 9 is 1.2 GB unpacked (≈ 0.77 GB compressed) of dead weight for this engine;
  FR-002 forbids libraries the engine never loads. faster-whisper's docs list cuDNN 9 because older
  ctranslate2 releases needed it.
- **Risk and guard**: a future ctranslate2 may start loading cuDNN. The local GPU check (R6) runs
  the engine on the GPU in the built image and fails if a library is missing; Dependabot bumps of
  ctranslate2/faster-whisper are followed by running it. `nvidia-cuda-nvrtc-cu12` arrives as a
  transitive dependency of `nvidia-cublas-cu12` (≈ 0.2 GB unpacked, ≈ 0.1 GB compressed); it is
  not loaded either, but excluding a transitive dependency needs a resolver override hack, so it
  stays (YAGNI).
- **Superseded by T022**: the image now leaves nvrtc out with `uv sync --no-install-package
  nvidia-cuda-nvrtc-cu12` on both sync steps, which needs no resolver override.
- **Alternatives considered**: `nvidia/cuda:*-cudnn-runtime` base (today; 3.15 GB base);
  `nvidia/cuda:*-base` + pip libraries (speaches; adds the CUDA base for nothing, the host driver
  is injected by the NVIDIA container toolkit); cuBLAS + cuDNN via pip (linuxserver; 1.83 GB
  measured for our image).

## R3. Runtime base and Python

- **Decision**: `python:3.14-slim` (Debian 13) as the runtime base, using its Python
  (`UV_PYTHON_DOWNLOADS=never`, the venv created from `/usr/local/bin/python3`); `uv` copied from
  its image for the build only; `uv sync` with a BuildKit cache mount
  (`--mount=type=cache,target=/root/.cache/uv`) so no cache lands in a layer.
- **Superseded by T023**: `uv` is no longer copied into the image; each `uv sync` step bind-mounts
  it from a named `uv` stage (`--mount=from=uv,source=/uv,target=/usr/local/bin/uv`), so the
  runtime image ships no uv binary.
- **Rationale**: 0.04 GB compressed base; one Python instead of base Python + uv-managed Python;
  the cache mount keeps rebuilds fast without shipping 0.45 GB.
- **GPU wiring**: `find /app/.venv -path "*/nvidia/*/lib" -type d > /etc/ld.so.conf.d/nvidia.conf
  && ldconfig` registers the pip-installed libraries with the dynamic linker (what speaches does);
  `NVIDIA_VISIBLE_DEVICES=all` and `NVIDIA_DRIVER_CAPABILITIES=compute,utility` make the NVIDIA
  container toolkit inject the host driver when a GPU is requested (what the `nvidia/cuda` images
  set). Without a GPU nothing changes: ctranslate2 reports 0 CUDA devices and the existing
  `WHISPER_DEVICE=auto` logic uses the CPU.
- **Dependabot**: the docker ecosystem would propose `python:3.15-slim`; Python minor upgrades
  need wheel availability checks (CLAUDE.md gotcha), so ignore `semver-major`/`semver-minor`
  updates for `python` and let the four version pins move together by hand.

## R4. ffmpeg

- **Decision**: Debian's `ffmpeg` package (7.1 on Debian 13), installed with
  `--no-install-recommends`.
- **Rationale**: 436 MB unpacked versus 288 MB for a static build (`mwader/static-ffmpeg` 9.0): the
  0.15 GB saving does not justify a second pinned image to keep current, while Debian's package
  gets security fixes with every base-image rebuild. Both decode every format VideoKit needs; the
  app only writes WAV (`pcm_s16le`), MP3 (`libmp3lame`) and JPEG (`mjpeg`) and uses the `scale`,
  `select` and `showinfo` filters.
- **Verification**: the smoke test gains a codec check inside the image: generate tiny clips with
  the image's own ffmpeg (H.264/AAC, HEVC/AAC, VP9/Opus, AV1/Opus, H.264/MP3) and run the app's
  probe + extraction on each (FR-006).

## R5. Measuring the compressed size

- **Decision**: `docker save <image> | gzip -6 | wc -c`, compared with a single limit (1.25 GB =
  1 250 000 000 bytes) held in `scripts/image_size_guard.sh`.
- **Evidence**: for `nvidia/cuda:12.9.2-cudnn-runtime-ubuntu24.04` this gives 3.148 GB against the
  registry's 3.15 GB layer total; the throwaway slim image measured 1.06 GB without cuDNN (2.1 GB
  unpacked).
- **Rationale**: works on any local or CI build without a registry; the limit leaves ≈ 15 %
  headroom for the app code, frontend bundle and dependency growth. CI's `image` job and the
  release run it after the smoke test.

## R6. Local GPU check

- **Decision**: `scripts/gpu_check.py`, bind-mounted read-only into the built image by
  `task docker:gpu-check` (`docker run --rm --gpus all --user $(id -u):$(id -g)
  -v ./.data:/data`), so no check code ships in the product. It fails with one line if
  `ctranslate2.get_cuda_device_count()` is 0, generates 5 s of audio with ffmpeg, loads Whisper
  `tiny` with `device="cuda"`, transcribes, and prints device count and timings. The model is
  cached under `/data/huggingface` (`./.data`).
- **Evidence**: warm runs took 2.1 s wall clock (load 0.65 s, transcribe 0.33 s); the first run
  6.5 s including the model download; without `--gpus` it printed "no GPU visible".
- **Testing**: the script's logic is unit-tested with fake `ctranslate2`/`faster_whisper` modules
  (no GPU in CI); the real run is the author's manual check (FR-009).

## R7. Changelog and version computation

- **Decision**: git-cliff (added to `.mise.toml`), `.cliff.toml` adapted from the author's
  earlier release setup (same header/body templates and skip rules: merges, `chore`, `test`,
  `style`, `build(deps…)`, `ci(deps…)`; groups Features / Bug Fixes / Others; commit links to the
  repository), plus `[bump] initial_tag = "v0.1.0"` and `breaking_always_bump_major = false`.
  `task changelog` runs `git-cliff --unreleased --bump --prepend CHANGELOG.md`, or `--tag $TAG`
  when `TAG` is set, and refuses when there are no releasable commits since the last tag.
- **Evidence**: in a scratch repository git-cliff 2.14.2 with `initial_tag` produced `v0.1.0` with
  no tags present (the author's earlier release setup needed an explicit `TAG` for its first
  release), then `v0.2.0` after a `feat`. Without `breaking_always_bump_major = false` a breaking
  change jumps 0.x to `v1.0.0`; the author wants to choose 1.0.0 deliberately (`TAG=v1.0.0`).
- **Release-notes scripts**: `.github/scripts/extract-changelog.sh` (prints one version's section,
  fails when missing or empty) and `.github/scripts/check-release-version.sh` (the tag must equal
  the newest `## ` heading), both taken from the author's earlier release setup, plus
  `.github/scripts/check-tag-on-main.sh` (`git merge-base --is-ancestor "$sha" origin/main`).

## R8. Release workflow

- **Decision**: `.github/workflows/release.yml`, triggered by `push: tags: ['v[0-9]+.[0-9]+.[0-9]+']`.
  1. `checks` job: cheap guards first — tag format, tag on main, tag equals the newest changelog
     entry, release notes extract.
  2. `ci` job: `uses: ./.github/workflows/ci.yml` (CI gains `on: workflow_call`), so the release
     runs exactly what CI runs: lint, unit tests with coverage gate, browser tests, hadolint,
     image build + smoke + size guard, secret scan.
  3. `publish` job (`needs: [checks, ci]`): `docker/setup-buildx-action`, `docker/login-action`
     (ghcr.io, `GITHUB_TOKEN`), `docker/metadata-action` (`type=semver,pattern={{version}}`,
     `type=raw,value=latest`, OCI labels), `docker/build-push-action` with `load: true` → smoke
     test + size guard on that exact image → `docker/build-push-action` again with `push: true`
     from the same GitHub Actions build cache (identical layers), `actions/attest` (build
     provenance, pushed to the registry), `softprops/action-gh-release` with the extracted notes
     plus a `docker pull` line.
- **Rationale**: one definition of "the checks" (no drift between CI and release); the image that
  is pushed is rebuilt from cache and re-checked, so nothing unchecked is published; the release
  page is created last, so a failed run leaves at most image tags that a re-run overwrites.
- **Details**: actions pinned to moving major tags like the existing workflows; `permissions`
  narrowed per job (`contents: write`, `packages: write`, `id-token: write`, `attestations: write`
  only on `publish`); `concurrency: release-${{ github.ref }}` without cancel-in-progress (CI's own
  group name differs, so the called workflow does not deadlock); image name lowercased from
  `github.repository`. CI's badge and PR-comment steps already condition on `main` pushes and pull
  requests, so they are skipped when called from a tag.
- **Workflow linting**: `actionlint` (new mise tool, part of `task lint`) checks both workflows;
  the release workflow cannot otherwise be exercised before a real tag.
- **Visibility**: a package first published by a workflow may be created private; RELEASING.md
  covers switching it to public once in the package settings (spec clarification).

## R9. Version

- **Decision**: the tag is the only version. `backend/pyproject.toml` keeps `version = "0.0.0"`
  with a comment (the build backend requires the field); `frontend/package.json` drops `version`
  (`"private": true` makes it optional); `videokit.__version__` and its test are removed (nothing
  reads them). The release labels the image with the version through `docker/metadata-action`.

## R10. Identity and documentation

- **Decision**: author "Anton Krivenko" in `LICENSE`, `pyproject.toml` `authors`, `package.json`
  `author` and the image label `org.opencontainers.image.authors`. Constitution v1.1.0 → v1.2.0
  (MINOR: Principle I explicitly allows the author's name in authorship places; the packaging
  constraint describes the slim single image and the compose file with GPU enabled by default);
  Principle I also allows the repository's own public addresses (`github.com/toshik1978/videokit`,
  `ghcr.io/toshik1978/videokit`) in links and configuration — the compose file, README and
  changelog commit links need them. `.claude/CLAUDE.md` mirrors both. Display name "VideoKit": `en.json` `app.title`,
  `index.html` `<title>` and `apple-mobile-web-app-title`, `manifest.webmanifest` `name` and
  `short_name` (and their tests), README, new docs and CLAUDE.md prose; lowercase identifiers
  (`videokit` package, image, repo, storage keys such as `videokit.uploads`) stay.
- **CONTRIBUTING.md and docs/RELEASING.md**: modeled on the author's other projects (sections: no
  PRs/issues, prerequisites, getting started, the gate, conventions, never commit live data, releases;
  RELEASING: how versions work, step 0 history sweep before the first push, changelog, preview,
  commit, tag, what the automation does, making the package public, retrying).
