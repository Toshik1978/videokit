# Contract: Commands

Taskfile tasks and scripts this feature adds or changes. Exit code 0 means success; every failure
prints one line to stderr naming the cause (no stack traces).

## Taskfile

| Task | Behaviour |
| --- | --- |
| `task changelog` | prepends the next release's section to `CHANGELOG.md` with git-cliff, computing the version; `TAG=vX.Y.Z` overrides it. Fails, leaving the file unchanged, when there are no releasable commits since the last tag |
| `task release:verify TAG=vX.Y.Z` | runs `check-release-version.sh` and `extract-changelog.sh` for `TAG` (the same guards the release runs), for use before tagging |
| `task docker:build` | unchanged: builds `videokit:local` from source |
| `task docker:run` | unchanged: runs `videokit:local` with `./.data` as `/data` as the host user; `GPU=true` adds `--gpus all`, whether given as a task variable (`task docker:run GPU=true`) or as an environment variable (`GPU=true task docker:run`) |
| `task docker:size IMAGE=…` | runs `scripts/image_size_guard.sh` on an image (default `videokit:local`) |
| `task docker:gpu-check` | runs `scripts/gpu_check.py` inside `videokit:local` with `--gpus all`, the host user and `./.data` as `/data` |

## Scripts

| Script | Arguments | Success | Failure (exit 1) |
| --- | --- | --- | --- |
| `scripts/image_size_guard.sh` | `<image>` | prints `image size: <GB> GB (limit 1.25 GB)` | prints measured size and limit; exit 2 on usage errors |
| `scripts/gpu_check.py` | none (runs inside the image) | prints `GPU check passed: <n> GPU(s), model load <s>, transcription <s>` | `no GPU visible to the container` / `GPU visible but CUDA unavailable (driver too old?)` (no CUDA device, but `/dev/nvidia*` exists) / `GPU libraries failed to load: <exception type>` (library import, or a model-load `ImportError`, `OSError` or `RuntimeError` mentioning CUDA, cuBLAS or cuDNN) / `could not generate test audio: <exception type>` / `model could not be loaded: <exception type>` (any other model-load error, such as a failed model download) / `model is not on the GPU (device: <device>)` / `transcription on the GPU failed: <exception type>` |
| `scripts/docker_smoke.sh` | `[image]` | as today, plus `codecs ok` after decoding H.264/AAC, HEVC/AAC, VP9/Opus, AV1/Opus and H.264/MP3 test clips through the app's media code | names the clip that failed |
| `.github/scripts/extract-changelog.sh` | `<tag> [changelog]` | prints the tag's section | `no '## <tag>' section found`; exit 2 on usage errors |
| `.github/scripts/check-release-version.sh` | `<tag> [changelog]` | prints `release version: <tag> (tag <tag>)` | tag differs from the newest `## ` heading (prints both); exit 2 on usage errors |
| `.github/scripts/check-tag-on-main.sh` | `<sha> [branch-ref]` (default `origin/main`) | prints `<sha> is on main` | `<sha> is not on main` |
