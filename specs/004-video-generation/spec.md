# Feature Specification: Video Generation Tool

**Feature Branch**: `004-video-generation`

**Created**: 2026-10-29

**Status**: Draft

**Input**: User description: "Video Generation — a third VideoKit tool. A Shorts creator (working
mostly on a phone) adds an optional, ordered list of illustration pictures and writes a prompt
such as 'create a video where picture 2 ...'; the prompt may reference pictures by position or be
standalone. The tool generates one short silent clip she downloads or shares and cuts into her own
edit (Shorts, sometimes full horizontal videos). One clip per job, never audio; 5–10 s; vertical
9:16 (default) or horizontal 16:9; 1080p (default) or 720p; estimated cost before submitting;
Download, Share and Try again; each attempt a new job; a Recent clips list kept on the device;
ordinary jobs created without a video upload; a running generation survives a restart without
paying twice; Google's Gemini Omni Flash as the single provider of a new fallback-ready provider
chain; distinct readable failures including out of credit; no spending cap; no in-app
authentication; prompts and pictures never logged."

## Clarifications

### Session 2026-10-29

- Q: Should Video Generation get its own way of creating jobs while YouTube Prep and Audio Extract
  keep the current one, or should all tools share one refactored flow? → A: One flow for all
  tools: each tool creates its jobs through its own creation endpoint built on shared platform
  building blocks (create a job, resumable upload, upload-complete handling, restart handling).
  The shared "create job" endpoint is removed; existing tools move to the new flow with unchanged
  behaviour for their users.
- Q: Should Video Generation jobs run one at a time, or several at the same time? → A: Up to 3
  Video Generation jobs generate at once; further jobs wait in the tool's line.
- Q: When no video provider is configured, should the Video Generation card still appear? → A: No.
  The card and the tool are hidden; its direct link shows the usual "not found" page.
- Q: Should the server keep a job's pictures for the whole retention period so Try again works
  later, or delete them after generation? → A: Keep them with the job until it expires (as YouTube
  Prep keeps its transcript for Regenerate); they are deleted with the job.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Generate a clip from a prompt and pictures (Priority: P1)

A creator is assembling a Short about a topic and needs a few seconds of illustrative footage.
On her phone she opens VideoKit, chooses Video Generation, adds three illustrations from her
gallery in the order she wants, and writes "slow zoom into picture 1, then picture 3 fades in over
it". She keeps the defaults (vertical, 1080p), picks 8 seconds, sees the estimated cost, and taps
Generate. When the clip is ready she downloads it, or shares it straight to her editing app, and
cuts it into her Short. Another time she writes a prompt with no pictures at all ("aerial shot of
a misty pine forest at dawn") and gets a clip from the text alone.

**Why this priority**: This is the whole tool; every other story only makes it more convenient.

**Independent Test**: Submit a prompt with two pictures and one without pictures against a fake
provider; verify each job goes queued → generating → done, the clip has the chosen duration,
orientation and resolution, has no audio track, and downloads with a sensible name.

**Acceptance Scenarios**:

1. **Given** the Video Generation page, **When** the user adds pictures, reorders them, removes
   one, writes a prompt and taps Generate, **Then** the pictures are sent in the order shown, each
   labelled by its position (picture 1, picture 2, …), and the page shows the job's stage.
2. **Given** the form, **When** the user changes duration, orientation or resolution, **Then** the
   estimated cost shown updates for the chosen options before anything is submitted.
3. **Given** a prompt and no pictures, **When** the user taps Generate, **Then** a clip is
   generated from the text alone.
4. **Given** a completed job, **When** the user looks at the result, **Then** they see a preview of
   the clip, its duration, orientation, resolution and actual cost, and a Download button (plus
   Share where the browser can share files).
5. **Given** a completed job, **When** the downloaded clip is played, **Then** it contains no audio
   track, the provider was asked for the chosen duration, orientation and resolution, and the result
   page shows the clip's actual duration, orientation and resolution.
6. **Given** a queued or generating job, **When** the user cancels it, **Then** the job shows as
   cancelled and its data is deleted; for a job already generating, the page warns beforehand that
   the provider may still charge for the attempt.

---

### User Story 2 - Leave and come back to finished clips (Priority: P2)

Generation takes minutes, so after tapping Generate the creator switches to her editor. Later she
reopens VideoKit and finds the clip under "Recent clips" at the top of the tool page, ready to
download, even if her phone's browser dropped the tab in the meantime or the server restarted
while the clip was being generated.

**Why this priority**: Without it a clip whose tab was closed is effectively lost although it was
paid for; but Story 1 already works when she keeps the page open.

**Independent Test**: Start two jobs, close the page, reopen the tool page; verify both appear in
Recent clips with their current status. Restart the server during generation; verify the job
continues and completes with a single provider request.

**Acceptance Scenarios**:

1. **Given** jobs started on this device, **When** the user opens the tool page, **Then** "Recent
   clips" lists them newest first, each with its status (queued, generating, ready, failed,
   cancelled), a thumbnail when ready, and the start of its prompt.
2. **Given** a ready clip in the list, **When** the user taps it, **Then** its result page opens
   with Download, Share and Try again.
3. **Given** an entry whose job has expired or no longer exists on the server, **When** the tool
   page loads, **Then** the entry is removed from the list without an error.
4. **Given** the server restarts while a job is queued or generating, **When** it comes back,
   **Then** the job continues from where it was and completes, and the provider is never asked to
   generate the same attempt twice.
5. **Given** the tool page, **When** it loads, **Then** it states that clips are deleted after the
   retention period (shown in hours as configured by the operator).

---

### User Story 3 - Try again with the same inputs (Priority: P3)

The clip is close but not right. The creator taps Try again and lands on the form with the same
pictures in the same order and the same prompt and options already filled in. She tweaks a few
words and generates again. The earlier clip stays available as its own entry in Recent clips.

**Why this priority**: It saves re-picking pictures from the gallery on every attempt, which is
the common case, but each attempt can also be started from scratch.

**Independent Test**: From a completed job, tap Try again; verify the form holds the job's
pictures (same order), prompt and options; submit; verify a new, independent job is created and
the original is unchanged.

**Acceptance Scenarios**:

1. **Given** a completed, failed or cancelled job still within retention, **When** the user taps
   Try again, **Then** the form opens prefilled with that job's pictures in their order, its prompt
   and its options.
2. **Given** a prefilled form, **When** the user submits it, **Then** a new job is created and
   charged on its own, and the original job and its clip are unchanged.

---

### User Story 4 - Video Generation on the home page and its readiness (Priority: P4)

A user opening VideoKit sees Video Generation as a tool card next to YouTube Prep and Audio
Extract, and opens it with one tap. An operator who never configured video generation still gets
a working VideoKit without the tool: no card and no dead end.

**Why this priority**: The tool is reachable by its direct link without the card; readiness only
matters to operators who don't use the tool.

**Independent Test**: Open the home page with and without a configured video provider; verify the
card is shown and opens the tool, and that without a provider VideoKit starts, the card is absent
and the tool's link shows the "not found" page.

**Acceptance Scenarios**:

1. **Given** the home page, **When** it loads, **Then** it shows the Video Generation card
   alongside the other tools, and selecting it opens the tool page.
2. **Given** no video generation provider is configured, **When** VideoKit starts, **Then** it
   starts normally, the other tools work, the home page shows no Video Generation card, and the
   tool's link and creation endpoint answer "not found".
3. **Given** a configured video provider that is unusable (for example a malformed key), **When**
   VideoKit starts, **Then** startup fails with one readable configuration error line, as for the
   other providers.

---

### Edge Cases

- Prompt references a picture that doesn't exist ("picture 5" with three pictures): the request is
  sent as written; the model decides. No validation of references is attempted.
- More than the maximum number of pictures, a picture over the size limit, or a file that is not a
  supported picture: the form refuses it before upload with a readable message; the server
  enforces the same limits.
- Empty prompt (with or without pictures): Generate is disabled; the server rejects it too.
- Prompt in any language or script: accepted and sent unchanged.
- Phone photos in the camera's own format (HEIC on iPhones): accepted.
- Provider account out of credit or quota exhausted: the job fails with a message telling the user
  the provider account needs topping up; no retry loop.
- Provider safety filter blocks the prompt or a picture: the job fails with a message saying the
  provider refused the content and suggesting a change to the prompt or pictures.
- Provider takes longer than the maximum wait: the job fails with a timeout message.
- Provider returns a clip with a different duration or resolution than requested: the clip is
  delivered as returned and its actual properties are shown.
- Server restart in the instant between sending the request and recording the provider's
  reference: the job fails with a readable message rather than resubmitting (never pay twice).
- The provider has already discarded the result when a restarted server asks for it: the job fails
  with a readable message.
- Browser storage unavailable (private mode, cleared site data): Recent clips is empty; the
  current job page still works by its link.
- More than 20 recent clips on the device: the oldest entries drop off the list (their jobs still
  exist on the server until they expire).
- Clip started on a phone, page opened on a laptop: the laptop's Recent clips doesn't show it; the
  job link still opens it.
- A YouTube Prep or Audio Extract job running at the same time: Video Generation jobs wait only in
  their own line, and the queue position counts only Video Generation jobs.
- Four clips submitted in a row: three generate at once, the fourth shows queue position 1 and
  starts when one of the three finishes, fails or is cancelled.

## Requirements *(mandatory)*

### Functional Requirements

**Video Generation tool**

- **FR-001**: The system MUST provide a Video Generation tool that takes a prompt and zero or more
  ordered pictures and produces exactly one video clip per job.
- **FR-002**: The user MUST be able to add pictures from the device's gallery or files, see them
  as thumbnails in order, reorder them and remove them before submitting.
- **FR-003**: The tool MUST accept up to 7 pictures per job, each at most 10 MB, in JPEG, PNG, WebP
  or HEIC; anything else is refused with a readable message, both in the form and on the server.
- **FR-004**: The prompt MUST be required, at most 2,000 characters, and sent to the provider
  unchanged; the pictures MUST be sent in the user's order, identified by their position so the
  prompt can refer to "picture 1", "picture 2" and so on.
- **FR-005**: The user MUST be able to choose a duration from 5 to 10 seconds (limited to the
  durations the provider supports in that range), orientation vertical 9:16 (default) or
  horizontal 16:9, and resolution 1080p (default) or 720p.
- **FR-006**: The clip MUST never contain audio: the provider is asked for silent output where it
  supports that, and any audio track it returns MUST be removed before the clip is offered.
- **FR-007**: The form MUST show the estimated cost for the chosen options before submitting; when
  the price of the chosen options is unknown it MUST say so instead of showing a number.
- **FR-008**: A completed job MUST show a preview of the clip, its duration, orientation,
  resolution and actual cost, a Download button, and a Share button wherever the browser supports
  sharing files (as in Audio Extract).
- **FR-009**: The downloaded clip MUST be a common MP4 video that plays in phone galleries and
  video editors, named from the start of the prompt (unsafe characters replaced, non-Latin
  characters preserved) with a fallback of `clip.mp4`.
- **FR-010**: The job MUST show its current stage: queued (with position), generating, done,
  cancelled, or failed (with a readable reason).
- **FR-011**: Failures MUST have distinct readable messages at least for: provider account out of
  credit or quota exhausted, content refused by the provider's safety filter, provider timeout,
  invalid or unsupported pictures, and provider unavailable.
- **FR-012**: Try again MUST open the form prefilled with the job's pictures (same order), prompt
  and options for any job still within retention; submitting it creates a new, independent job.
- **FR-013**: The tool page MUST show a "Recent clips" list of jobs started on this device, newest
  first, with status, a thumbnail of ready clips and the start of the prompt; tapping an entry
  opens its job page.
- **FR-014**: The Recent clips list MUST be kept only on the device, hold at most 20 entries
  (oldest dropped first), drop entries older than the server's retention period, and drop entries
  whose job the server reports as expired or missing. When device storage is unavailable the list
  is simply empty. The server MUST NOT offer a way to list all jobs.
- **FR-015**: The tool page MUST state that clips are deleted after the retention period, using
  the operator's configured value.
- **FR-016**: Cancelling a generating job MUST warn beforehand that the provider may still charge
  for the attempt.

**Platform (extended where needed)**

- **FR-017**: Every tool MUST create its jobs through its own creation endpoint, built on shared
  platform building blocks: creating a job, a resumable chunked upload for a job, handling of a
  completed upload chosen by the tool (for example the playable-video check), and handling of jobs
  interrupted by a restart chosen by the tool (fail, as today, or resume). A job may need no upload
  and go straight to the queue. All jobs share the job store, stages, cancellation, job links,
  retention and purge. The shared job-creation endpoint is removed; YouTube Prep and Audio Extract
  move to the new flow with no change in behaviour visible to their users (same upload, resume,
  size limit, playable-video check and restart message). Adding a tool with a new kind of input
  MUST NOT require platform changes.
- **FR-018**: The platform MUST provide video generation as a provider capability behind an
  ordered, operator-configured provider list with fallback, like transcription and the language
  model. The first provider is Google's Gemini Omni Flash. The provider actually used is recorded
  with the job.
- **FR-019**: Fallback to the next provider MUST happen only when the current provider fails
  before accepting the request; once a provider has accepted an attempt, the system MUST NOT send
  the same attempt to another provider.
- **FR-020**: Queued and generating Video Generation jobs MUST survive a server restart: queued
  jobs stay queued, and generating jobs resume waiting for the provider's result using the
  provider's reference recorded when it accepted the request. A restart MUST NOT cause a second
  paid request for the same job.
- **FR-021**: Provider readiness checks at startup and on health requests MUST NOT make paid calls.
  With no video provider configured, VideoKit MUST start normally and the tool MUST be hidden: no
  home page card, and its page and endpoints answer "not found".
- **FR-022**: Job cost MUST be computed from the provider's reported usage with the platform's
  pricing table (operator overrides included); an unknown price yields an unknown cost, as for the
  other tools.
- **FR-023**: Up to 3 Video Generation jobs MUST be able to generate at the same time; further
  jobs wait in the tool's own first-in-first-out line, independent of the other tools' lines, and
  their queue position counts only Video Generation jobs waiting. The other tools keep running one
  job at a time.
- **FR-024**: Pictures, prompt and clip MUST be stored only with the job's data, used only for that
  job and its Try again, and deleted with the job at the end of the configurable retention period
  or on cancel. Pictures are deliberately kept after generation (not deleted as soon as the
  provider has them) so Try again works for the whole retention period.
- **FR-025**: Logs and user-facing messages MUST NOT contain prompts, pictures, clips, credentials
  or internal error traces. The job API MUST return the prompt and pictures only for that job's own
  result and Try again.
- **FR-026**: Every user-facing string of the tool MUST come from the interface translation
  catalog; provider and server error texts MUST NOT be shown as the primary message.
- **FR-027**: The tool MUST be fully usable on screens from 360 px wide in current iOS Safari and
  Android Chrome: no horizontal scrolling, touch targets at least 44×44 px, including picture
  reordering and removal.
- **FR-028**: No spending limit is enforced; the operator controls spending through the provider
  account. No in-app authentication is added.

### Key Entities

- **Video Generation job**: a job of the shared job system for this tool; holds the stage, the
  prompt, the options (duration, orientation, resolution), the ordered pictures, the provider used,
  the provider's reference for a running attempt, cost, and on success the clip's actual duration,
  orientation and resolution.
- **Picture**: an input image of a job with its position (1-based); kept with the job's data until
  the job is deleted.
- **Clip**: the silent MP4 produced by a job, plus a thumbnail taken from it; downloadable while
  the job is retained.
- **Recent clip entry**: a device-only record of a job started on that device (job reference,
  start time, start of the prompt); never sent to or stored on the server.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: From opening the home page, a user can submit a prompt with pictures in 6 taps or
  fewer, excluding typing and the device's own picture picker.
- **SC-002**: 100 % of delivered clips contain no audio track and match the chosen orientation.
- **SC-003**: A server restart during generation never causes a second paid provider request for
  the same job, and the job completes after the restart whenever the provider still holds the
  result.
- **SC-004**: After leaving the app, a user finds a clip they started on the same device in Recent
  clips within 2 taps of opening VideoKit, for the whole retention period.
- **SC-005**: The estimated cost shown before submitting is within 20 % of the job's actual cost
  for jobs whose price is known.
- **SC-006**: Every failure caused by the provider account running out of credit shows the "top up
  the provider account" message, never a generic error.
- **SC-007**: No job data — pictures, prompt, clip or thumbnail — remains more than 1 hour after the
  retention period ends.
- **SC-008**: The complete flow, including reordering pictures, can be done on a 360 px-wide phone
  in both iOS Safari and Android Chrome without horizontal scrolling or zooming.

## Assumptions

- The user is an individual creator making Shorts and sometimes full videos; clips are b-roll she
  edits elsewhere, so she never needs audio, stitching of several clips, or in-app editing.
- Pictures are mostly illustrations rather than photos of real people, so provider restrictions on
  real people are not a primary concern; such refusals surface as the content-refused failure.
- Generation takes on the order of minutes per clip; queued and running jobs are expected to be
  few at a time.
- Each attempt costs on the order of a dollar at 1080p, verified against the provider's official
  pricing before prices are recorded; the operator tops up a prepaid provider account as needed.
- One video generation provider (Google's Gemini Omni Flash, via a Google AI Studio key) is enough
  for the first version; further providers can be added to the list later without changing the
  tool.
- The operator protects the endpoint (reverse proxy or tunnel with access control), as the README
  already requires for VideoKit; no extra protection is added for a tool that spends money.
- Retention is the existing platform setting; the tool adds no retention setting of its own.
- Image-to-image editing, longer videos, multiple clips per job, version history, audio, and
  cross-device job history are out of scope.
