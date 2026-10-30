# Feature Specification: Remote Transcription Server

**Feature Branch**: `005-remote-transcription`

**Created**: 2026-10-30

**Status**: Draft

**Input**: User description: "Remote speech-to-text: replace VideoKit's in-process faster-whisper
transcription with a provider for any OpenAI-compatible transcription server (e.g. speaches, a
self-hosted faster-whisper server), and remove local Whisper entirely. Operators who already run a
shared Whisper server on the same host should not run a second Whisper inside VideoKit — it
duplicates the model, holds about 2 GB of GPU memory and forces a GPU-capable image. Keep the paid
OpenAI provider. Breaking change accepted: no zero-config transcription default any more."

## Clarifications

### Session 2026-10-30

- Q: If an operator configures no transcription provider at all, should VideoKit refuse to start, or
  start with YouTube Prep hidden? → A: Refuse to start with a `Configuration error:` line until a
  transcription provider is set (same as the text-generation providers).
- Q: Should a self-hosted server be a separate provider from OpenAI, given both speak the same API?
  → A: No. The existing `openai` transcription provider gets a configurable address
  (`OPENAI_TRANSCRIBE_URL`, empty = OpenAI's API) and an optional key
  (`OPENAI_TRANSCRIBE_API_KEY`); `OPENAI_TRANSCRIBE_MODEL` already exists. Cost follows the
  address: OpenAI's API is priced, any other address is a self-hosted server at 0. A single
  transcription address means no fallback from the self-hosted server to OpenAI, which is accepted.
- Q: Should VideoKit delete the leftover Whisper model files in its data directory after the
  upgrade? → A: No; VideoKit leaves them, and the release notes tell operators to delete the model
  cache themselves.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Transcribe through a self-hosted server (Priority: P1)

An operator already runs a speech-to-text server that speaks the OpenAI transcription API (for
example speaches) on the same machine as VideoKit. They point VideoKit's `openai` transcription provider at it
with an address and a model name, and YouTube Prep transcribes every uploaded video there. Chapters, titles and the output
language come out exactly as before, the job shows no transcription cost, and VideoKit itself uses
no GPU memory.

**Why this priority**: This is the reason for the feature; without it the operator keeps two
copies of the same model on one GPU.

**Independent Test**: Configure VideoKit with a fake OpenAI-compatible server, upload a sample
video with speech, and verify the job completes with timed chapters, the detected language and a
transcription cost of 0.

**Acceptance Scenarios**:

1. **Given** VideoKit configured with only the self-hosted server for transcription, **When** a
   user runs YouTube Prep on a video with speech, **Then** the job completes with chapters whose
   times follow the speech, the text is in the spoken language, and the transcription part of the
   cost is 0 (never "unknown").
2. **Given** a self-hosted server whose model is unloaded (cold), **When** a job reaches
   transcription, **Then** VideoKit waits for the server to load the model and the job completes
   instead of timing out.
3. **Given** a long video whose audio exceeds a single upload, **When** it is transcribed through
   the self-hosted server, **Then** all of it is transcribed and the timestamps run continuously
   from start to end.
4. **Given** a video without speech, **When** it is transcribed through the self-hosted server,
   **Then** the result is the same as today: English output and the no-speech notice.
5. **Given** a self-hosted address configured with `OPENAI_TRANSCRIBE_API_KEY`, **When** VideoKit
   transcribes, **Then** that key is sent to the server and appears in no log line, health response
   or UI text.
6. **Given** a self-hosted address and an `OPENAI_API_KEY` (for text generation) but no
   `OPENAI_TRANSCRIBE_API_KEY`, **When** VideoKit transcribes, **Then** the OpenAI key is never sent
   to the self-hosted server.
7. **Given** no address configured, **When** VideoKit transcribes, **Then** it uses OpenAI's API with
   `OPENAI_TRANSCRIBE_API_KEY` (or else `OPENAI_API_KEY`) and the job reports OpenAI's per-minute
   cost, exactly as before.

---

### User Story 2 - A lighter, GPU-free image (Priority: P2)

An operator deploys VideoKit on any host, with or without a GPU. The image no longer carries a
speech model runtime or GPU libraries, so it downloads faster, uses less disk, and needs no GPU
passthrough, no GPU drivers in the container and no model download cache.

**Why this priority**: It is the second payoff of removing local transcription, but the operator
already gets the memory saving from Story 1.

**Independent Test**: Build the image, check that it contains no speech-model runtime or GPU
libraries, that its compressed size is well below the previous release's, and that the smoke test
passes without any GPU option.

**Acceptance Scenarios**:

1. **Given** the new image, **When** it is inspected, **Then** it contains no local speech-model
   runtime and no GPU libraries.
2. **Given** the shipped Compose file, **When** an operator starts it on a host without NVIDIA
   tooling, **Then** it starts without editing anything GPU-related.
3. **Given** the documentation, **When** an operator reads how to configure transcription,
   **Then** it describes the self-hosted server and OpenAI options and no GPU setup.

---

### User Story 3 - Clear configuration for operators upgrading (Priority: P3)

An operator upgrading from a release with local transcription still has the old settings (the
`local` provider, the Whisper model and device settings). VideoKit tells them exactly what to change
instead of failing obscurely or silently ignoring it.

**Why this priority**: It only matters once, at upgrade, but without it the breaking change is
confusing.

**Independent Test**: Start VideoKit with `TRANSCRIBE_PROVIDERS=local` and with `openai` listed but
neither an address nor an OpenAI key; verify startup stops with one readable configuration-error line per
problem, naming the variables but not their values.

**Acceptance Scenarios**:

1. **Given** `TRANSCRIBE_PROVIDERS` still listing `local`, **When** VideoKit starts, **Then**
   startup fails with a configuration error saying `local` was removed and how to transcribe instead.
2. **Given** `openai` listed with no `OPENAI_TRANSCRIBE_URL` and no OpenAI key, **When** VideoKit
   starts, **Then** startup fails with one configuration-error line naming the missing key variables.
   With an address set, no key is required.
3. **Given** no transcription provider configured at all, **When** VideoKit starts, **Then** startup
   fails with a `Configuration error:` line saying `TRANSCRIBE_PROVIDERS` must list at least one
   provider; VideoKit never runs without transcription.
4. **Given** leftover Whisper model or device settings, **When** VideoKit starts, **Then** they are
   ignored and do not affect startup.
5. **Given** the release notes of the version that ships this change, **When** an operator reads
   them, **Then** they find the breaking change and the migration steps.

### Edge Cases

- The self-hosted server is unreachable or returns an error: the job fails with the existing "all
  transcription providers failed" message; the log names the error type only.
- `OPENAI_TRANSCRIBE_URL` is set to OpenAI's own API address: it counts as OpenAI's API (priced, and
  `OPENAI_API_KEY` may be used).
- The server returns the language as a name ("english") or as an ISO code ("en"): both are
  understood; an unknown value counts as "language not detected".
- The server returns no segments: treated as no speech.
- The configured address has or lacks a trailing slash or the `/v1` suffix: the operator follows
  the documented form; the documentation shows one exact example.
- The health check runs while the server is down or its model is unloaded: the check stays cheap
  and never transcribes; it reports configuration only.
- Audio Extract and Video Generation behave exactly as before, whatever the transcription
  configuration.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The `openai` transcription provider MUST work with any server that implements the
  OpenAI audio transcription API: the operator MAY set its base address (`OPENAI_TRANSCRIBE_URL`);
  empty means OpenAI's API. `openai` is the only transcription provider.
- **FR-002**: The operator MUST be able to set the model (`OPENAI_TRANSCRIBE_MODEL`, default
  `whisper-1`) and MAY set a transcription key (`OPENAI_TRANSCRIBE_API_KEY`). OpenAI's API uses
  `OPENAI_TRANSCRIBE_API_KEY`, else `OPENAI_API_KEY`, and requires one of them; any other address
  uses only `OPENAI_TRANSCRIBE_API_KEY` and requires none — `OPENAI_API_KEY` MUST NOT be sent to it.
- **FR-003**: The provider MUST deliver what YouTube Prep uses today: segment-level start and end
  times, segment text, the detected language and the audio duration. Word-level timing and
  progress reporting are not required.
- **FR-004**: Audio longer than one upload MUST be transcribed completely, with segment times
  continuous across the whole audio.
- **FR-005**: A transcription request MUST tolerate a server that first loads its model; the wait
  limit MUST be long enough for a cold load plus the transcription of the longest supported video.
- **FR-006**: Cost MUST follow the address, not the model name: OpenAI's API is priced per minute
  as today; any other address is priced at 0, so a job's cost never becomes unknown because of a
  self-hosted server (whatever its model is called).
- **FR-007**: The provider's startup and health check MUST make no transcription request and no
  paid call; it reports the configured model and whether the address is self-hosted, never the address or a
  key.
- **FR-008**: The API key, the audio and the transcript MUST NOT appear in logs, health output, API
  responses or the UI; failures are logged by error type only.
- **FR-009**: The local transcription provider MUST be removed, together with everything that exists
  only for it: the speech-model runtime and its GPU libraries, the Whisper model and device
  settings, GPU detection, GPU passthrough in the shipped Compose file, the model download cache,
  the GPU check command and script, and the GPU sections of the documentation.
- **FR-010**: Existing `openai` transcription configurations (no address, `OPENAI_API_KEY`) MUST keep
  working unchanged.
- **FR-011**: Startup MUST fail with one `Configuration error:` line per problem when
  `TRANSCRIBE_PROVIDERS` lists `local` or an unknown provider, when OpenAI's API is used without a
  key, when `OPENAI_TRANSCRIBE_URL` is not an `http(s)://` address, or when no transcription
  provider is configured.
- **FR-012**: There MUST be no default transcription provider that works without configuration;
  the documentation and `.env.example` MUST show how to configure the self-hosted server and
  OpenAI.
- **FR-013**: The default test suites MUST still run without network access to any provider, using
  fakes; a real-server smoke test MAY exist behind the `live` marker.
- **FR-014**: The change MUST be released as a version whose release notes mark it as breaking and
  list the migration steps (settings removed, settings added, GPU no longer needed, and deleting
  the leftover model cache by hand). VideoKit itself MUST NOT delete the old model cache.

### Key Entities

- **Transcription provider `openai`**: an optional base address (empty = OpenAI's API), a model and
  an optional transcription key; produces a timestamped transcript with the detected language;
  priced when it uses OpenAI's API, free otherwise.
- **Transcript**: segments with start, end and text, language, audio duration, no-speech flag, the
  provider and model that produced it, and whether it came from a self-hosted server (for cost).

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: On a host running a shared speech-to-text server, VideoKit holds 0 MB of GPU memory
  at any time, including during YouTube Prep jobs.
- **SC-002**: The same sample videos produce chapters, titles and output language through the
  self-hosted server that match the previous local-transcription results in count and language
  (timings within a few seconds).
- **SC-003**: The compressed image is at least 40 % smaller than the previous release's.
- **SC-004**: A YouTube Prep job whose transcription goes through a cold server (model not loaded)
  completes without a timeout.
- **SC-005**: 100 % of the jobs transcribed through the self-hosted server show a known cost.
- **SC-006**: An operator upgrading with old settings gets a startup message naming every variable
  to change, and can fix the configuration in one pass.

## Assumptions

- The self-hosted server implements the OpenAI transcription endpoint with the `verbose_json`
  response and segment timestamps (speaches does).
- The server runs on the same host or network, so audio stays on the operator's machines; it is the
  operator's job to run and protect it.
- Keeping the existing upload chunking for the self-hosted server is harmless and keeps one code
  path; a server without OpenAI's upload limit simply receives fewer, larger requests.
- Leftover `WHISPER_MODEL` / `WHISPER_DEVICE` variables are ignored rather than rejected, so an
  upgrade fails only on settings that actually change behaviour.
- Audio Extract and Video Generation never transcribe and are out of scope.
- Pre-1.0 versioning: the breaking change ships as the next minor version (0.4.0).
