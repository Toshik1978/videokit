# Changelog

What changed in each VideoKit release and why. Summaries are written by hand; the commit lists
under them are generated with [git-cliff](https://git-cliff.org) via `task changelog`.

Versions follow [semver](https://semver.org). Commits follow
[Conventional Commits](https://www.conventionalcommits.org).

---

## v0.3.0 — 2026-10-30

VideoKit gets its third tool, Video Generation: write a prompt, optionally add up to seven pictures
(JPEG, PNG, WebP or HEIC) in the order the prompt refers to them, and get one silent 5–10 s MP4
clip, 9:16 or 16:9, at 1080p or 720p, to download, share or try again with the same inputs. The
estimated cost is shown before you start (about US$0.10 per second at 720p and US$0.15 at 1080p),
up to three clips generate at once, and recent clips are remembered on the device. It uses Gemini
Omni through a Google AI Studio key and stays hidden until you set `VIDEO_PROVIDERS=omni` and
`GEMINI_API_KEY`; nothing else needs configuring. A restart resumes a running generation instead
of paying for it twice. Breaking change for scripts that call the API directly: `POST /api/jobs` is
gone, and jobs are now created with `POST /api/tools/<id>/jobs`. The web app already uses the new
route.

### Features

- feat: tool-owned job creation and video generation building blocks ([d8fa603](https://github.com/toshik1978/videokit/commit/d8fa603d6d3f534c015fe4ac8d4ed3206f47c6bf))
- feat: generate a short silent clip from a prompt and pictures ([c7f6728](https://github.com/toshik1978/videokit/commit/c7f6728ae088937b5522787a1677e2ac59754827))
- feat: resume interrupted generations and list recent clips on the device ([5e63a03](https://github.com/toshik1978/videokit/commit/5e63a03fc374e2cf5229fe12d2d0697062213a92))
- feat: try again with the same pictures, prompt and options ([8e1cb64](https://github.com/toshik1978/videokit/commit/8e1cb64a92a088c21d214ec763cd6bb5fbf07f50))
- feat: show the Video Generation card and hide the tool when it is not configured ([0eb8439](https://github.com/toshik1978/videokit/commit/0eb8439e27fcc59e83b3f1ee24022ff63e52cd71))

### Bug Fixes

- fix: show the clip's actual properties, play everywhere and reorder pictures by drag ([b99b3bc](https://github.com/toshik1978/videokit/commit/b99b3bcd314e094f1bc6979f834fd015a042efcd))
- fix: never charge twice after a restart and download 1080p clips by URI ([7a6d352](https://github.com/toshik1978/videokit/commit/7a6d35274d47b99fffbfe611672c917d99d297ed))
- fix: show a refused prompt as refused, not as a failed clip ([933ce05](https://github.com/toshik1978/videokit/commit/933ce05b464d4cbaa488a0dc62c57049826cb738))

### Others

- docs: document Video Generation and check the silent clip in the image ([a29cacd](https://github.com/toshik1978/videokit/commit/a29cacd9f3665d6f2e8ea961fb7c8f3eed741090))

---

## v0.2.0 — 2026-10-14

VideoKit gets its second tool, Audio Extract: upload a video and download its main audio track as
a 192 kbps MP3 named after the video, or on a phone share it straight to apps such as Telegram. It
uses no AI provider and costs nothing. A video without sound ends with a clear "no audio" message.
Each tool now has its own job queue, so an MP3 never waits behind a YouTube Prep job. No
configuration changes are needed: the job database gains a column on first start. An upload that
was interrupted before the upgrade will not resume; pick the file again.

### Features

- feat: add queue lanes, file names and MP3 encoding to the platform ([ec39e5c](https://github.com/toshik1978/videokit/commit/ec39e5c1fdc9a66c365bf11e25eb2cff99e1f66b))
- feat: add the Audio Extract tool: upload a video, download or share its MP3 ([b41ad6c](https://github.com/toshik1978/videokit/commit/b41ad6ced4d7fc9a391d331d97cd8c5e48d94f2b))
- feat: tell the user when a video has no audio ([aa4f42a](https://github.com/toshik1978/videokit/commit/aa4f42a021fde31fcfebd310fcbae1aebf81c2e7))
- feat: show Audio Extract on the home page ([8962a3b](https://github.com/toshik1978/videokit/commit/8962a3bcb1913110dba42c6ba76e7cf400ead68e))

### Bug Fixes

- fix: keep 192 kbps for low-rate audio, neutral extracting labels and the audio.mp3 fallback ([8eca164](https://github.com/toshik1978/videokit/commit/8eca164b4a558722f5ec01a7f8e489876539d74e))
- fix: resume uploads only on their own tool and tidy Audio Extract downloads ([d65a32c](https://github.com/toshik1978/videokit/commit/d65a32cba3f0f0cb1b558ef8b55f918ecf45e596))

### Others

- docs: document Audio Extract and check MP3 encoding in the image ([710e226](https://github.com/toshik1978/videokit/commit/710e2267cb7cdf542d24cff98e14e60b7c4ab17e))

---

## v0.1.0 — 2026-10-11

The first release of VideoKit, a self-hosted toolbox of video tools in one Docker container. Its
first tool, YouTube Prep, takes an uploaded video and returns cover titles, a title and
description, tags and hashtags, and chapter timings in the video's spoken language, with
per-section regenerate. Transcription runs locally with Whisper (on an NVIDIA GPU when one is
available, otherwise on the CPU) or through OpenAI; text comes from Anthropic or OpenAI. Run it
with `docker compose up -d` using `ghcr.io/toshik1978/videokit:0.1.0` or `latest`, after copying
`.env.example` to `.env` and adding your API keys; delete the GPU block in `compose.yaml` on hosts
without the NVIDIA container toolkit. There is no authentication: put it behind your own access
control.

### Features

- feat: initial setup with spec, tooling, CI and core foundations ([c8c3b39](https://github.com/toshik1978/videokit/commit/c8c3b39e1e8b9efd8b15cad8a5b419218bf613cb))
- feat(youtube-prep): upload a video and get YouTube metadata (MVP) ([4be92c2](https://github.com/toshik1978/videokit/commit/4be92c2b15d8be709f0f1b02e998d2c589443b87))
- feat(youtube-prep): regenerate a single section with an optional hint ([514a6b9](https://github.com/toshik1978/videokit/commit/514a6b939db3f3365ba43d68257beade85adcea7))
- feat: upload large videos reliably through proxies with resume ([181af5c](https://github.com/toshik1978/videokit/commit/181af5c3b5fd5a3e59dfdabe1ad30b06cf491621))
- feat(frontend): use videokit from a phone and install it to the home screen ([2c86506](https://github.com/toshik1978/videokit/commit/2c86506cf2ece3f58f955d779e6634b0d10f5df5))
- feat: OpenAI providers, ordered fallback, startup checks and usage footer ([4fae36d](https://github.com/toshik1978/videokit/commit/4fae36d1830ddd8c8ccd4c03db2b26f209f7dd6c))
- feat(frontend): add toolbox home page with tool cards ([3ddc7d0](https://github.com/toshik1978/videokit/commit/3ddc7d0ba54fc723ee32e29e860dea4e1674ba50))
- feat: VideoKit identity, author name and tag-only version ([ddf5738](https://github.com/toshik1978/videokit/commit/ddf5738d850cf8ad9124d9dc03849aa695080334))

### Bug Fixes

- fix(deps): make local Whisper audio decoding work with the resolved av version ([d547944](https://github.com/toshik1978/videokit/commit/d547944ce0916806fc4489f8fbe528085a389ebe))
- fix(youtube-prep): strip decorative ASCII symbols from chapter titles ([2affcb1](https://github.com/toshik1978/videokit/commit/2affcb18abb3d9fae825ae1310d1e5a5fb57e48a))
- fix: close convergence gaps in logging, i18n, expiry and hooks ([56bd944](https://github.com/toshik1978/videokit/commit/56bd94408cfb51cedf93cbeb0373bb086ed1984f))
- fix: hide lifespan tracebacks, reject expired uploads, clarify expiry message ([d9d2a7a](https://github.com/toshik1978/videokit/commit/d9d2a7a6010df1f2bb8e8bda0b7a161ed3d639e1))
- fix: pass the secret scan and skip expired uploads at startup ([df79162](https://github.com/toshik1978/videokit/commit/df79162c52033bedfbbc1e5d85190e2fb80539c6))
- fix: address final review (Docker HF cache, detected output language, validation, chapter tail, 360 px e2e) ([9ff7b16](https://github.com/toshik1978/videokit/commit/9ff7b162f7fdfd937eb1dbb1e41eb1358b32e8a0))
- fix: use PUID/PGID for the compose user and document creating ./.data first ([0975bf0](https://github.com/toshik1978/videokit/commit/0975bf0a842d34a6dd316d80ec53a9f25d21c7f6))
- fix: close the convergence gaps in the image, release labels and docs ([efeb908](https://github.com/toshik1978/videokit/commit/efeb908b3ba44b4793e7ba43015a7e6e7f461537))
- fix: skip frames past the last decodable video frame ([922d581](https://github.com/toshik1978/videokit/commit/922d5817dac37ec0ed629024efcee0835c5189e9))
- fix: close the second convergence round and read the Matroska duration tag as an end ([8f3099f](https://github.com/toshik1978/videokit/commit/8f3099fb23726e81d66c03d79e6fc32be0470266))
- fix: let the changelog tests pass after a release and allow the release commit on main ([333901e](https://github.com/toshik1978/videokit/commit/333901ee6f99a14db51abc68df514083eddf5219))
- fix: fail extraction when no frame is written, whatever ffmpeg exits with ([f9a152d](https://github.com/toshik1978/videokit/commit/f9a152d5734beb3959c47b3250b90e11c6badcd7))

### Others

- build: Docker packaging, live tests, README and quickstart validation ([fa54402](https://github.com/toshik1978/videokit/commit/fa544022048d1e7075e0adbca3e5b340e5931cd5))
- build: store runtime data in a gitignored ./.data bind mount and add docker Taskfile tasks ([56c6d9b](https://github.com/toshik1978/videokit/commit/56c6d9bc362959a56ea327016fca0799df7e05cf))
- build: move to Python 3.14, mise-action v5, uv 0.13 and CUDA 12.9.2 ([0350221](https://github.com/toshik1978/videokit/commit/0350221c372b8db3e2879da614032640a5d5ca76))
- build: slim single image for CPU and GPU hosts ([d454c0a](https://github.com/toshik1978/videokit/commit/d454c0a98c06f503788d9b5b70752139c57d1213))
- ci: tag-driven release flow with changelog and ghcr.io publishing ([772ae3d](https://github.com/toshik1978/videokit/commit/772ae3d90bcd6b9aee5ea7815afbc1636c1efb4d))
- docs: published image, README, contributing and compose with GPU ([fa3ef35](https://github.com/toshik1978/videokit/commit/fa3ef35a06b47d6a7694bb710c902daae2408b96))
- docs: sync the agent guide and fix GPU=true for docker:run ([7f3e999](https://github.com/toshik1978/videokit/commit/7f3e999eed3a6e4e114352f380947a5ec18aebaa))
- build: pin uv in a named stage Dependabot can update ([f3ef438](https://github.com/toshik1978/videokit/commit/f3ef438d2995a324d678fa38d40f8f45730f220b))
- docs: point the CI badges at the project gist ([eb68393](https://github.com/toshik1978/videokit/commit/eb6839300e55a6ca5dc2f550ae22e1d31c982162))
- ci: keep Dependabot off TypeScript, av and Node majors that need manual work ([5feaf06](https://github.com/toshik1978/videokit/commit/5feaf06e1fccd61ce12ce063621aa9d6d8f48ac3))

---

