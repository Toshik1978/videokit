# Feature Specification: videokit Platform and YouTube Prep Tool

**Feature Branch**: `001-videokit-youtube-prep`

**Created**: 2026-10-11

**Status**: Draft

**Input**: User description: "videokit platform + first tool 'YouTube Prep' — a self-hosted web app
hosting multiple video-processing tools; YouTube Prep turns an uploaded video into ready-to-paste
YouTube metadata (cover title options, title, description, tags, hashtags, quoted chapter
timings), with resumable uploads, per-section regenerate, pluggable AI providers with fallback,
and automatic cleanup of uploaded media."

## Clarifications

### Session 2026-10-11

- Q: What language should the videokit interface itself be in? → A: English only for now, with all
  interface strings kept in a single translation catalog so languages can be added later without a
  rewrite.
- Q: Should users be able to edit the generated text in place before copying? → A: Yes, editable in
  the browser only; Copy uses the edited text, edits are not saved on the server, and Regenerate
  replaces that section's edits.
- Q: Should users be able to cancel a job that is queued or processing? → A: Yes, while uploading,
  queued or running; a running job stops at the next stage boundary and its data is deleted
  immediately.
- Q: Must videokit be usable from mobile phones? → A: Yes. Fully usable in current mobile browsers
  (iOS Safari, Android Chrome) with a responsive layout, uploads from the phone's gallery or
  camera roll, and installable to the home screen as a web app; no native app.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Get YouTube metadata for a video (Priority: P1)

A video creator opens videokit, chooses the YouTube Prep tool and uploads a finished video (for
example, a 20-minute hotel review). While it uploads and processes, they see progress and the
current stage. When processing finishes, they see six result sections — cover title options,
title, description, tags, hashtags and chapter timings — in the language spoken in the video,
each with a one-click Copy button, ready to paste into YouTube.

**Why this priority**: This is the entire reason the product exists; without it nothing else has
value.

**Independent Test**: Upload a short sample video with speech and visible scene changes; verify all
six sections appear, are in the spoken language, respect length limits, and the chapters follow
the required format and rules.

**Acceptance Scenarios**:

1. **Given** the YouTube Prep page, **When** the user uploads a valid video with speech, **Then**
   the page shows upload progress, then the stages queued → extracting → transcribing →
   generating, and finally the six result sections.
2. **Given** a completed job, **When** the user views the chapters section, **Then** every line has
   the form `<time> "<title>"` (e.g. `0:00 "Arrival and lobby"`), the first line starts at `0:00`,
   there are at least 3 lines, consecutive chapters are at least 10 seconds apart, times ascend and
   are within the video duration, and titles contain no dashes, emojis or decorative symbols.
3. **Given** a completed job, **When** the user presses Copy on any section, **Then** that section's
   exact text is placed on the clipboard and a brief confirmation is shown.
4. **Given** a completed job, **When** the user edits a section's text and presses Copy, **Then** the
   edited text is copied.
5. **Given** a video whose speech is in a given language, **When** processing completes, **Then**
   all generated text is in that same language.
6. **Given** a completed job, **When** the user views the results, **Then** the cover titles
   section shows 3 options of at most 5 words each, the title is at most 100 characters, the
   description is 2–4 short paragraphs and at most 5000 characters without chapters embedded,
   tags total at most 500 characters, and there are 3–5 hashtags.
7. **Given** another job is already processing, **When** the user uploads a video, **Then** their
   job waits in a queue and the page shows its position until processing starts.
8. **Given** a job that is queued or running, **When** the user cancels it, **Then** the job shows
   as cancelled (a running job after its current stage finishes), its data is deleted, and the
   next queued job starts.

---

### User Story 2 - Refine a single section (Priority: P2)

The creator likes most of the result but wants a punchier title or chapters with different
names. They press Regenerate on that section, optionally type what to change ("shorter, mention the
pool"), and get a new version of only that section, quickly and cheaply, without re-processing the
video.

**Why this priority**: Generated text rarely fits perfectly on the first try; refining one section
avoids a full rerun and keeps the rest intact.

**Independent Test**: On a completed job, regenerate the title with a hint; verify only the title
changes, the hint is reflected, and the result arrives much faster than the original run.

**Acceptance Scenarios**:

1. **Given** a completed job, **When** the user regenerates one section with a hint, **Then** only
   that section is replaced, the other sections remain unchanged, and the new text reflects the
   hint.
2. **Given** a completed job, **When** the user regenerates a section without a hint, **Then** a
   different alternative for that section is produced.
3. **Given** a regenerated chapters section, **When** it is displayed, **Then** it satisfies all
   chapter format and rule requirements from User Story 1.
4. **Given** a job older than the retention period, **When** the user tries to regenerate, **Then**
   they are told the job has expired and must upload again.

---

### User Story 3 - Upload large videos reliably through a proxy (Priority: P2)

The operator exposes videokit through a tunnel or reverse proxy that limits the size of a single
request (for example, to 100 MB). A creator uploads a multi-gigabyte, 90-minute video over an
unstable connection; the upload survives network interruptions and resumes without starting over.

**Why this priority**: Without this, typical videos cannot be uploaded at all in the intended
deployment.

**Independent Test**: Behind a proxy with a 100 MB request limit, upload a file of several
gigabytes; interrupt the connection midway and confirm the upload resumes from where it stopped.

**Acceptance Scenarios**:

1. **Given** a proxy with a 100 MB per-request limit, **When** the user uploads a 5 GB video,
   **Then** the upload completes successfully.
2. **Given** an upload in progress, **When** the connection drops and returns within the session,
   **Then** the upload resumes from the last confirmed point.
3. **Given** a file larger than the configured maximum, **When** the user selects it, **Then** it is
   rejected before upload with a message stating the limit.
4. **Given** a file that is not a playable video, **When** upload completes, **Then** the job fails
   with a clear "not a supported video" message.

---

### User Story 4 - Operator deploys and configures providers (Priority: P3)

An operator runs videokit as a single container on their own machine — with or without a GPU —
and chooses which AI services to use for transcription and text generation, in priority order.
If the preferred service fails, the next one is used automatically. Misconfiguration is reported
clearly at startup.

**Why this priority**: Required for anyone besides the original author to run the tool, and for
resilience, but the core value is delivered once a single working configuration exists.

**Independent Test**: Start the app with a primary provider configured to fail and a working
fallback; process a video and verify it completes using the fallback and the result footer names
it. Start with a provider listed but missing credentials and verify startup reports it.

**Acceptance Scenarios**:

1. **Given** a host without a GPU, **When** local transcription is configured, **Then** jobs still
   complete (more slowly).
2. **Given** an ordered provider list, **When** the primary provider errors, **Then** the next
   provider is used and the job completes without user action.
3. **Given** a provider listed in configuration without required credentials, **When** the app
   starts, **Then** it refuses to start and names the misconfigured provider.
4. **Given** a running app, **When** the readiness status is requested, **Then** it reports each
   configured provider and whether it is ready.
5. **Given** a completed job, **When** the user views the result footer, **Then** it shows which
   transcription and text-generation providers were used and the run's token usage and estimated
   cost.

---

### User Story 5 - Toolbox home page (Priority: P3)

A user opening videokit sees a home page listing the available tools as cards with a name and a
short description, and opens a tool from there. Today only YouTube Prep is listed; future tools
appear automatically when added.

**Why this priority**: Establishes videokit as a toolbox rather than a single-purpose app, but
delivers little value on its own while only one tool exists.

**Independent Test**: Open the home page and verify YouTube Prep is listed and opens its page;
register a dummy tool in a test build and verify it appears without changes to shared code.

**Acceptance Scenarios**:

1. **Given** the home page, **When** it loads, **Then** every registered tool is shown as a card
   with name and description.
2. **Given** the home page, **When** the user selects the YouTube Prep card, **Then** the YouTube
   Prep page opens.

---

### User Story 6 - Use videokit from a phone (Priority: P2)

A creator who filmed on their phone opens videokit in the phone's browser (or from its home-screen
icon), picks the video from the gallery, uploads it, locks the phone while it processes, and later
reopens the job to edit and copy the results — all with comfortable touch controls.

**Why this priority**: Many videos are shot and published from a phone; a desktop-only tool
would force an extra transfer step.

**Independent Test**: On a 360 px-wide phone screen in iOS Safari and Android Chrome, complete the
full flow (select from gallery → upload → wait → edit → copy each section) and add the app to the
home screen.

**Acceptance Scenarios**:

1. **Given** a phone browser, **When** the user opens any videokit page, **Then** the layout fits
   the screen width without horizontal scrolling and all controls are comfortably tappable.
2. **Given** the YouTube Prep page on a phone, **When** the user chooses a file, **Then** they can
   pick a video from the phone's gallery or files.
3. **Given** an upload in progress on a phone, **When** the browser is backgrounded or the screen
   locks and the user returns, **Then** the upload resumes; if the page was reloaded, re-selecting
   the same file resumes from the last confirmed point.
4. **Given** a completed job on a phone, **When** the user presses Copy, **Then** the text is copied
   to the phone's clipboard (or, where the browser disallows programmatic copy, the text is
   selected so the user can copy it with one gesture).
5. **Given** a supported phone browser, **When** the user chooses "Add to Home Screen", **Then**
   videokit installs with its own name and icon and opens full-screen.

---

### Edge Cases

- Video with no speech (music only or silent): processed using visuals only; results carry a
  notice that no speech was detected; language defaults to English
  (superseded for silent videos by 006 FR-007).
- Video shorter than 45 seconds (3 chapters 10 s apart, the last one 10 s before the end, need
  30 s, and below 45 s the model kept missing that fit): metadata is produced and the chapters
  section states that the video is too short for chapters.
- Chapters that still break FR-027/FR-028 after the repair attempt while every other section is
  valid: the job completes without chapters and with a `chapters_omitted` notice; regenerating the
  chapters clears it.
- Video longer than 1 hour: chapter times use `h:mm:ss` from the first chapter at or past one
  hour; earlier chapters keep `m:ss` (`0:00`, `59:30`, `1:02:10`).
- Model returns chapters that break the rules (not starting at 0:00, too close, out of order,
  beyond duration): one automatic repair attempt; if still invalid, the job fails with a readable
  reason rather than showing non-conforming chapters.
- Chapter title from the model contains dashes, emojis, decorative symbols or double quotes: these
  are removed or replaced before display so the output format is always exact.
- Generated text exceeds a length limit: one automatic repair attempt; if still too long, the job
  fails with a readable reason.
- All configured providers for a stage fail: the job fails, naming the stage and a concise reason;
  no keys or internal details are shown.
- User closes the browser during processing: processing continues; reopening the job link shows
  current status or results until the job expires.
- Upload abandoned midway: partial data is removed automatically after the retention period.
- Mixed-language speech: the language detected by transcription is used for all output.
- Server restarts while jobs are queued or processing: those jobs are marked failed with a
  "server restarted, please upload again" message.

## Requirements *(mandatory)*

### Functional Requirements

**Platform**

- **FR-001**: The system MUST provide a home page listing all registered tools with name and short
  description, each linking to the tool's page.
- **FR-002**: Adding a new tool MUST NOT require changes to shared platform functionality.
- **FR-003**: The system MUST accept video uploads via a resumable, chunked transfer in which no
  single request exceeds 50 MB, so that uploads succeed behind proxies limiting request bodies to
  100 MB.
- **FR-004**: The system MUST reject files larger than a configurable maximum (default 10 GB)
  before transfer begins, and reject files that are not playable videos after upload, each with a
  clear message.
- **FR-005**: The system MUST process one job at a time; additional jobs MUST wait in a
  first-in-first-out queue, and the user MUST see their queue position.
- **FR-006**: The system MUST show each job's current stage: uploading (with percentage), queued
  (with position), extracting, transcribing, generating, done, cancelled, or failed (with a
  readable reason).
- **FR-007**: Each job MUST have a shareable link that shows its current status or results for as
  long as the job is retained.
- **FR-008**: The system MUST extract from each video a timestamped transcript of its speech and a
  set of timestamped representative frames (at most a configurable number, default 80) that cover
  scene changes across the whole video.
- **FR-009**: The system MUST detect the spoken language of the video.
- **FR-010**: Transcription and text generation MUST each be performed by a configurable, ordered
  list of providers; on a provider failure the next provider MUST be tried automatically, and the
  provider actually used MUST be recorded with the job.
- **FR-011**: Supported transcription providers MUST include a local option that works with or
  without a GPU, and a cloud option; supported text-generation providers MUST include at least two
  independent cloud AI vendors (Anthropic and OpenAI), with Anthropic as the default.
- **FR-012**: The system MUST validate configuration at startup and refuse to start with a clear
  message naming any configured provider that lacks required credentials or resources.
- **FR-013**: The system MUST expose a readiness status listing each configured provider and
  whether it is ready.
- **FR-014**: The uploaded source video MUST be deleted as soon as extraction completes (or the job
  fails).
- **FR-015**: Job data (transcript, frames, results) MUST be retained for a configurable period
  (default 24 hours) to allow regeneration, then deleted automatically; incomplete uploads MUST be
  deleted after the same period.
- **FR-016**: Logs and user-facing messages MUST NOT contain credentials, transcript text, or
  internal error traces.
- **FR-017**: The application MUST NOT include authentication; documentation MUST state that
  operators are responsible for protecting access.
- **FR-018**: All configuration MUST be supplied through environment variables, documented with
  placeholder example values.
- **FR-019**: Jobs that were queued or processing when the server stopped MUST be marked failed
  with a message asking the user to upload again.
- **FR-036**: The user interface (labels, buttons, messages) MUST ship in English, with every
  user-facing interface string drawn from a single translation catalog so that adding a language
  requires only a new catalog, not code changes; generated metadata language is independent of the
  interface language (see FR-020).
- **FR-037**: Users MUST be able to cancel a job while it is uploading, queued or running. A queued
  or uploading job is cancelled immediately; a running job stops at the next stage boundary
  (after extracting, transcribing or generating) and the next queued job starts. All data of a
  cancelled job MUST be deleted immediately.
- **FR-038**: All pages MUST be fully usable on screens from 360 px wide in current iOS Safari and
  Android Chrome: no horizontal scrolling, touch targets at least 44×44 px, file selection from the
  device gallery/files.
- **FR-039**: videokit MUST be installable to a phone's home screen as a web app (name, icon,
  standalone display).
- **FR-040**: When the browser does not allow programmatic clipboard access (e.g. a non-HTTPS
  origin), Copy MUST fall back to selecting the section text and telling the user to copy it.

**YouTube Prep tool**

- **FR-020**: For each processed video, the tool MUST produce, in the detected spoken language
  (superseded by 006: the creator can pick the output language):
  3 cover title options, a title, a description, tags, hashtags and chapters.
- **FR-021**: Cover title options MUST each be at most 5 words.
- **FR-022**: The title MUST be at most 100 characters.
- **FR-023**: The description MUST be 2–4 paragraphs, at most 5000 characters, and MUST NOT include
  chapter timings.
- **FR-024**: Tags MUST be presented as a single comma-separated list whose total length is at most
  500 characters.
- **FR-025**: Hashtags MUST be 3–5 items, each starting with `#` and containing no spaces,
  presented separately from the description.
- **FR-026**: Chapters MUST be rendered one per line as `<time> "<title>"`, where time is `m:ss`
  for times under one hour and `h:mm:ss` from one hour on, followed by a single space and the
  title wrapped in straight double quotes.
- **FR-027**: Chapter titles MUST NOT contain dashes, emojis, decorative symbols or double quotes;
  the system MUST remove or replace such characters before display.
- **FR-028**: Chapters MUST satisfy: first chapter at 0:00; at least 3 chapters; each chapter at
  least 10 seconds after the previous one; strictly ascending; all within the video duration.
- **FR-029**: If generated output violates FR-021–FR-028, the system MUST make one automatic repair
  attempt; if output still violates them, the job MUST fail with a readable reason (except the
  too-short-video and chapters-only cases, see Edge Cases).
- **FR-030**: Each result section MUST be editable in place in the browser and MUST have a Copy
  action that copies exactly the section's current (possibly edited) text. Edits are not saved on
  the server and are lost on page reload; generated-output rules (FR-021–FR-028) are not enforced
  on user edits.
- **FR-031**: Each result section MUST have a Regenerate action with an optional free-text hint
  that replaces only that section (discarding any unsaved edits to it), reusing the already
  extracted transcript and frames.
- **FR-032**: The result view MUST show which providers were used and the run's token usage and
  estimated cost, including regenerations.
- **FR-033**: When no speech is detected, the tool MUST still produce results from visuals and show
  a notice that no speech was detected.

**Project hygiene**

- **FR-034**: No repository artifact (code, docs, specifications, task records, examples, test
  data, commit messages) may contain personal data, real hostnames, domains, site names, or
  secrets; examples MUST use placeholders.
- **FR-035**: The default automated test suite MUST run without AI provider credentials, network
  access to AI providers, or a GPU.

### Key Entities

- **Tool**: A video-processing capability offered by videokit. Attributes: identifier, display
  name, short description, page.
- **Upload**: An in-progress or completed file transfer. Attributes: identifier, total size, bytes
  received, created time.
- **Job**: One processing run of one tool on one uploaded video. Attributes: identifier, tool,
  stage, queue position, failure reason, created time, expiry time, video duration, detected
  language, providers used, token usage and estimated cost.
- **Media Artifacts**: Data derived from a video and kept for the job's lifetime: timestamped
  transcript segments and timestamped frames.
- **YouTube Prep Result**: The generated output of a job: cover title options, title, description,
  tags, hashtags, chapters (start time + title), and per-section regeneration history count.
- **Provider**: A configured transcription or text-generation service with its priority and
  readiness.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: For a 30-minute video on a host with a GPU or with cloud transcription, results are
  ready within 10 minutes after the upload completes.
- **SC-002**: 100% of displayed chapter sections conform to the required format and chapter rules;
  non-conforming output is never shown to the user.
- **SC-003**: A 5 GB video uploads successfully through a proxy with a 100 MB per-request limit,
  and an interrupted upload resumes without re-sending more than one chunk of already-sent data.
- **SC-004**: Regenerating a single section completes within 60 seconds and costs less than 20% of
  the full run's text-generation cost.
- **SC-005**: A user can go from opening the home page to having copied all six sections with no
  more than 9 clicks, excluding waiting time.
- **SC-006**: When the primary provider for a stage fails, at least 95% of affected jobs still
  complete via the fallback without user action.
- **SC-007**: No uploaded source video remains on disk after its job's extraction completes, and no
  job data remains more than 1 hour after the retention period ends.
- **SC-008**: An automated scan of the repository on every change finds zero secrets or personal
  identifiers.
- **SC-009**: Average text-generation cost per 30-minute video with the default provider stays
  under US$0.50.
- **SC-010**: The complete flow from opening the app to copying all six sections can be done on a
  360 px-wide phone in both iOS Safari and Android Chrome without horizontal scrolling or zooming.

## Assumptions

- Primary users are individual video creators; a single operator runs one instance for themselves
  or a small group. Concurrency of one job at a time is acceptable.
- Typical videos are 10–30 minutes; videos up to about 90 minutes must work; longer videos are not
  a target but are not blocked if under the size limit.
- Users want metadata in the same language as the video; translation and multi-language output are
  out of scope.
- The result is copied manually into YouTube; uploading or editing videos on YouTube is out of
  scope.
- Thumbnail image generation is out of scope; only cover title text is produced.
- No job history list is provided; the job link is the only way back to a job.
- The operator supplies AI provider credentials and accepts that, for cloud providers, extracted
  audio, frames and transcripts are sent to those providers.
- Mobile use is through the phone's web browser (optionally installed to the home screen); native
  mobile apps and sharing a video directly from the phone's share sheet are out of scope.
- The application runs as a single container; a GPU is optional and only speeds up local
  transcription.
- The project is published as open source under the MIT license.
- Cost figures are estimates based on providers' published per-token prices at the time of the run.
