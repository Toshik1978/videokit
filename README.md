# videokit

[![Backend tests](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/<gist-user>/<gist-id>/raw/backend-tests.json)](.github/workflows/ci.yml)
[![Backend coverage](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/<gist-user>/<gist-id>/raw/backend-coverage.json)](.github/workflows/ci.yml)
[![Frontend tests](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/<gist-user>/<gist-id>/raw/frontend-tests.json)](.github/workflows/ci.yml)
[![Frontend coverage](https://img.shields.io/endpoint?url=https://gist.githubusercontent.com/<gist-user>/<gist-id>/raw/frontend-coverage.json)](.github/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

videokit is an open-source, self-hosted web app that hosts small video tools. You run it as a
single Docker container on your own machine, with or without a GPU. The first tool is
**YouTube Prep**.

## YouTube Prep

Upload a finished video and get ready-to-paste YouTube metadata, written in the language spoken in
the video:

- **Cover titles**: 3 options, at most 5 words each.
- **Title**: at most 100 characters.
- **Description**: 2 to 4 short paragraphs, at most 5000 characters, without chapters embedded.
- **Tags**: one comma-separated list, at most 500 characters in total.
- **Hashtags**: 3 to 5, each starting with `#`.
- **Chapters**: one per line as `<time> "<title>"`, starting at `0:00`, with at least 3 chapters
  at least 10 seconds apart, for example:

  ```text
  0:00 "Arrival and lobby"
  ```

Other features:

- Resumable, chunked uploads (50 MB per request), so large videos survive flaky connections and
  request-size limits in front of the app.
- Every section is editable in the browser and has a Copy button. Edits are not saved on the
  server.
- Per-section Regenerate with an optional hint. It reuses the already extracted transcript and
  frames, so it is quick and cheap.
- Progress per stage (uploading, queued, extracting, transcribing, generating), a queue (one job at
  a time), cancel, and a shareable job link that works while the job is retained.
- Pluggable transcription and text-generation providers, tried in the order you configure, with
  automatic fallback. The result footer shows the providers used, token usage and estimated cost.

## No authentication: protect it yourself

**videokit has no login, no users and no access control.** Anyone who can reach the port can upload
videos, spend your provider credits and open any job link they know. Securing access is your
responsibility as the operator.

- Do not expose the port directly to the internet. Bind it to a trusted network, or put it behind
  something that authenticates users.
- A reverse proxy, a tunnel service or an authentication gateway (an identity-aware proxy, basic
  auth, SSO in front of the app, a VPN) all work. Use whichever you already run.
- Serve it over HTTPS if it is reachable beyond localhost. Browsers only allow clipboard access and
  home-screen install from a secure origin. On plain HTTP, Copy falls back to selecting the text.
- Uploads are sent in chunks of at most 50 MB, so a request-body limit of 64 to 100 MB or more on
  your proxy is enough. For example, nginx needs `client_max_body_size 100m;`. Make sure proxy
  timeouts are long enough for one chunk on a slow connection.
- Job ids are long random tokens, so links cannot be guessed, but anyone who has a link can see
  that job. Treat links as private.

## Quickstart

You need Docker with the Compose plugin.

```bash
cp .env.example .env     # then edit .env and fill in your API key placeholders
docker compose up -d --build
```

Then open <http://localhost:8000>. The service listens on port 8000 and keeps its data (jobs,
SQLite index, model cache) in the `/data` volume.

The default configuration transcribes locally and generates text with Anthropic, so set
`ANTHROPIC_API_KEY` in `.env`. The app refuses to start, with a message naming the problem, if a
listed provider lacks its key or model id. `GET /api/health` reports which configured providers
are ready.

## Configuration

Everything is configured with environment variables. `.env.example` has placeholder values; the full
reference is in
[`specs/001-videokit-youtube-prep/contracts/configuration.md`](specs/001-videokit-youtube-prep/contracts/configuration.md).
The most important variables:

| Variable | Default | Meaning |
| --- | --- | --- |
| `TRANSCRIBE_PROVIDERS` | `local` | Ordered, comma-separated: `local`, `openai`. The next one is tried if one fails. |
| `LLM_PROVIDERS` | `anthropic` | Ordered, comma-separated: `anthropic`, `openai`. The next one is tried if one fails. |
| `ANTHROPIC_API_KEY` | none | Required if `anthropic` is listed. |
| `OPENAI_API_KEY` | none | Required if `openai` is listed for either stage. |
| `OPENAI_MODEL` | none | Required if `openai` is in `LLM_PROVIDERS`. It has no default so a stale model id is never pinned. |
| `MAX_UPLOAD_GB` | `10` | Largest accepted upload. Larger files are rejected before transfer. |
| `JOB_TTL_HOURS` | `24` | How long job data is kept. |
| `WHISPER_DEVICE` | `auto` | `auto`, `cuda` or `cpu` for local transcription. |

Example of cloud-only transcription with an OpenAI fallback for text generation:

```bash
TRANSCRIBE_PROVIDERS=openai
LLM_PROVIDERS=anthropic,openai
ANTHROPIC_API_KEY=<your-anthropic-api-key>
OPENAI_API_KEY=<your-openai-api-key>
OPENAI_MODEL=<your-openai-model-id>
```

## GPU is optional

One image works on hosts with and without a GPU.

- **CPU only (default):** local transcription runs on the CPU. It is slower, but jobs complete.
- **GPU:** local transcription runs on an NVIDIA GPU. Install the NVIDIA Container Toolkit on the
  host and enable the GPU block in `compose.yaml` (the `deploy.resources.reservations.devices`
  section, commented out by default). With `WHISPER_DEVICE=cuda`, startup fails if no usable GPU is
  found; `auto` uses the GPU when present.
- **Cloud only:** set `TRANSCRIBE_PROVIDERS=openai` to skip local transcription entirely.

## Cost

Only the cloud providers you configure cost money. With the default configuration, text generation
is estimated to cost under US$0.50 per 30-minute video, and regenerating one section costs a small
fraction of that. Cheap regeneration relies on the provider's prompt cache, which lives about five
minutes and is refreshed by each use; a regeneration after a longer pause pays for the video's
transcript and frames again. Cloud transcription with OpenAI adds roughly US$0.006 per audio
minute; local transcription is free. Figures are estimates based on published per-token prices,
shown in each job's footer. If prices change, override them with `PRICING_OVERRIDES` (see the
configuration reference).

## Using it from a phone

videokit is built for phone browsers (current iOS Safari and Android Chrome) as well as desktop.
The layout fits screens from 360 px wide, and the file picker lets you choose a video from the
gallery or files. If the screen locks during an upload, it resumes when you return, and if the page
was reloaded, selecting the same file again continues from the last confirmed point. Processing
continues on the server while you are away; reopen the job link to see the results.

To install it, choose **Add to Home Screen** in the browser's share or menu. It opens full-screen
with its own name and icon. This needs HTTPS (see the authentication section above).

## Data lifecycle and privacy

- The uploaded source video is deleted as soon as extraction finishes, or when the job fails or is
  cancelled.
- Job data (transcript, frames, results) is kept for `JOB_TTL_HOURS` (default 24) so you can
  regenerate sections, then purged automatically. Incomplete uploads are removed after the same
  period. Cancelling a job deletes its data immediately.
- If the server restarts while jobs are queued or running, those jobs are marked failed and you
  need to upload again.
- There is no job history list; the job link is the only way back to a job.
- Logs and user-facing errors never contain API keys, transcript text or stack traces.

What leaves your machine depends on the providers you choose:

| Data | Where it goes |
| --- | --- |
| Audio | Stays on your machine with local transcription. Sent to OpenAI if `openai` is in `TRANSCRIBE_PROVIDERS`. |
| Transcript text and extracted frames | Sent to the provider(s) in `LLM_PROVIDERS` (Anthropic by default, OpenAI as an option) to generate and regenerate the text. |

The full video file is never sent to any provider. By using cloud providers you accept their terms
and data handling.

## Development

Prerequisites: [mise](https://mise.jdx.dev/) and `ffmpeg`.

```bash
mise install    # python, uv, node, task, gitleaks, hadolint, pre-commit
task setup      # backend (uv) and frontend (npm) dependencies
pre-commit install
git config merge.beads.driver "br merge-driver %O %A %B"
```

Day to day:

```bash
task lint      # ruff, ruff format, mypy, vue-tsc, ESLint, Prettier
task test      # backend and frontend tests with coverage (80 % line coverage floor)
task e2e       # Playwright end-to-end tests (desktop and phone emulation)
task format    # auto-format both stacks
```

- The default test suite needs no API keys, no network access to AI providers and no GPU; it uses
  fakes and tiny generated video fixtures.
- Tests that call real providers are opt-in. Provide keys in your environment and run
  `cd backend && uv run pytest -m live`.
- Commit messages follow [Conventional Commits](https://www.conventionalcommits.org/)
  (`feat:`, `fix:`, `docs:`, `chore:` and so on).
- Design documents live in [`specs/001-videokit-youtube-prep/`](specs/001-videokit-youtube-prep/):
  `spec.md`, `plan.md`, `research.md`, `data-model.md`, `quickstart.md` and the `contracts/`.
  The project principles are in
  [`.specify/memory/constitution.md`](.specify/memory/constitution.md).
- Never commit personal data, real hostnames, or secrets. Use placeholders such as `example.com` and
  `<your-api-key>`. CI scans the full history with gitleaks.

### Test and coverage badges

The badges at the top read JSON files from a GitHub gist that CI updates on every push to `main`.
Replace `<gist-user>` and `<gist-id>` in the badge URLs with your own gist owner and gist id. In
your fork, configure the `GIST_ID` secret (the gist id) and a `GIST_TOKEN` secret (a token with the
`gist` scope) so the workflow in [`.github/workflows/ci.yml`](.github/workflows/ci.yml) can publish
them.

## License

[MIT](LICENSE)
