# Quickstart: validating the slim image and the release flow

Run from the repository root after `mise install && task setup`. Contracts:
[commands](contracts/commands.md), [release workflow](contracts/release-workflow.md),
[image](contracts/image.md).

## 1. Image size and CPU behaviour (US1, FR-001–FR-007)

```bash
task docker:build
task docker:size                      # → image size: ~1.1 GB (limit 1.25 GB), exit 0
scripts/docker_smoke.sh videokit:local  # → codecs ok … smoke test passed
docker run --rm --entrypoint sh videokit:local -c 'ls /usr/local/cuda 2>&1; ldconfig -p | grep -c cudnn'
                                      # → no such directory; 0
```

CPU transcription (US1 scenario 2; needs network once for the Whisper `tiny` download, cached
under `./.data/huggingface`). Runs as the host user with `./.data` mounted:

```bash
mkdir -p .data
docker run --rm --user "$(id -u):$(id -g)" -v "$PWD/.data:/data" --entrypoint python videokit:local -c 'import subprocess, faster_whisper; subprocess.run(["ffmpeg", "-loglevel", "error", "-f", "lavfi", "-i", "sine=duration=5", "-ar", "16000", "-ac", "1", "/tmp/t.wav"], check=True); m = faster_whisper.WhisperModel("tiny", device="cpu", compute_type="int8"); list(m.transcribe("/tmp/t.wav")[0]); print("cpu transcription ok:", m.model.device)'
                                      # → cpu transcription ok: cpu
```

Expected: size under the limit; smoke test passes for the default user and the host user with a
bind-mounted `/data`; the CPU transcription completes without error; no CUDA toolkit or cuDNN in the image.

## 2. GPU (US1 scenario 3, FR-008/FR-009) — on a host with an NVIDIA GPU

```bash
task docker:gpu-check   # first run downloads Whisper "tiny" into ./.data
task docker:gpu-check   # → GPU check passed: 1 GPU(s), … in a few seconds
docker run --rm -v "$PWD/scripts/gpu_check.py:/gpu_check.py:ro" videokit:local python /gpu_check.py
                        # no --gpus → "no GPU visible to the container", exit 1
```

On a host without the NVIDIA container runtime, `task docker:gpu-check` fails earlier: Docker
refuses `--gpus all` with its own error before the script runs. The script's `no GPU visible to the
container` line appears only when the image runs without `--gpus`, as in the manual command above.

Then `GPU=true task docker:run` and `curl -s localhost:8000/api/health`: the local transcription
provider reports `device=cuda`.

## 3. Changelog and release guards (US2, FR-010–FR-014)

In a scratch clone (so the real `CHANGELOG.md` stays untouched):

```bash
task changelog                         # no tags yet → "## v0.1.0 — <date>" prepended
.github/scripts/extract-changelog.sh v0.1.0     # prints the section
.github/scripts/check-release-version.sh v0.1.0 # → release version: v0.1.0 (tag v0.1.0)
.github/scripts/check-release-version.sh v0.2.0 # → exit 1, names both versions
git add CHANGELOG.md && git commit -m "chore(release): v0.1.0"
git tag -a v0.1.0 -m v0.1.0
task changelog                         # nothing releasable since the tag → "nothing to release since v0.1.0", exit 1, file unchanged
```

## 4. First real release (US2 scenario 4, US3)

Follow `docs/RELEASING.md`: history sweep (first time only), `task changelog`, write the summary,
`task release:verify TAG=v0.1.0`, commit `chore(release): v0.1.0`, push `main`, push the tag.
Expected: the "Publish release" workflow's `checks`, `ci` and `publish` jobs pass; the package page
shows `0.1.0` and `latest` with an attestation; the release page shows the notes and the pull
command; after making the package public once, `docker pull` works without logging in.

Then on the GPU host: `mkdir -p .data && docker compose up -d` pulls `latest`, starts with the GPU
and reports healthy.

## 5. Identity and docs (US4, FR-016–FR-023)

```bash
grep -rn '"version"' frontend/package.json        # → nothing
grep -n '^version' backend/pyproject.toml         # → version = "0.0.0" with the tag comment
grep -n 'Anton Krivenko' LICENSE backend/pyproject.toml frontend/package.json Dockerfile
grep -rn '>videokit<\|"videokit"' frontend/index.html frontend/public/manifest.webmanifest frontend/src/i18n/en.json
                                                  # → nothing (display name is VideoKit)
```

Read README "About this project", CONTRIBUTING.md and docs/RELEASING.md; follow CONTRIBUTING's
getting-started steps in a fresh clone and get `task lint && task test` green.
