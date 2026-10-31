# Feature Specification: YouTube Prep Output Language

**Feature Branch**: `006-youtube-prep-language`

**Created**: 2026-10-30

**Status**: Draft

**Input**: User description: "YouTube Prep output language choice. Today the output language (cover
titles, title, description, tags/hashtags, chapter titles) is the language detected in the video's
speech, and a video with no speech always gets English (001 FR-020). Operators whose audience uses
one language need the output in that language even for silent videos, and occasionally a specific
language for one video. Add a language choice to YouTube Prep: on the upload page, a picker with
'Same as the speech' (default, today's behaviour) plus a list of languages each shown by its own
name; the device remembers the last choice so it is set once and forgotten; a picked language
always wins, even over the detected speech; with 'Same as the speech' and no speech, the output uses
the browser's language instead of always English; the existing no-speech notice names the language
used; regenerating a section keeps the job's language. The language is fixed at upload (changing it
means uploading again). Out of scope: a server-wide language setting, and switching the language of
a finished job. Nothing may name any specific person's languages; the feature is generic.
Supersedes 001 FR-020's 'no speech → English'."

## Clarifications

### Session 2026-10-30

- Q: Should the result page always show which language the results are written in, or only mention
  it in the no-speech notice? → A: Always show the output language on the result page (by its own
  name), plus in the no-speech notice.

## User Scenarios & Testing *(mandatory)*

### User Story 1 - Pick the output language once and keep it (Priority: P1)

A creator who publishes for an audience in one language opens YouTube Prep, picks that language in
the language choice next to the upload, and uploads a video. Cover titles, title, description, tags,
hashtags and chapter titles all come out in the picked language, whatever language (if any) is
spoken in the video. On later visits from the same device the same language is already selected, so
the creator never has to pick it again.

**Why this priority**: It is the reason for the feature: silent or foreign-language videos currently
come out in a language the creator does not publish in, and every result has to be rewritten.

**Independent Test**: Pick a language other than the one spoken in a sample video, upload it, and
verify every section of the result is in the picked language; reload the page and verify the
choice is still selected.

**Acceptance Scenarios**:

1. **Given** a language is picked, **When** a video with speech in a different language is
   processed, **Then** all output sections are written in the picked language.
2. **Given** a language is picked, **When** a video with no speech is processed, **Then** all
   output sections are written in the picked language and the no-speech notice names that
   language.
3. **Given** a language was picked on this device before, **When** the creator opens YouTube Prep
   again (new visit, page reload), **Then** that language is already selected.
4. **Given** a finished job whose language was picked, **When** the creator regenerates one
   section, **Then** the new section is in the same language as the rest of the job.
5. **Given** a finished job, **When** the creator views its results, **Then** the page shows the
   output language by its own name.

---

### User Story 2 - Keep "Same as the speech", with a sensible silent-video fallback (Priority: P2)

A creator who has never touched the language choice uploads videos as before. A video with speech
comes out in the spoken language, exactly as today. A video with no speech comes out in the
language of the creator's browser instead of always English, and the no-speech notice says which
language was used.

**Why this priority**: It keeps today's behaviour for everyone who does not use the new choice, and
removes the English-only fallback that started this feature.

**Independent Test**: With the choice on "Same as the speech", upload a video with speech and verify
the output follows the speech; then, from a browser set to a non-English language, upload a silent
video and verify the output is in that language and the notice names it.

**Acceptance Scenarios**:

1. **Given** "Same as the speech" is selected (the default on a device that never chose),
   **When** a video with speech is processed, **Then** the output is in the detected spoken
   language.
2. **Given** "Same as the speech" and a browser whose language is in the supported list,
   **When** a video with no speech is processed, **Then** the output is in the browser's language
   and the no-speech notice names it.
3. **Given** "Same as the speech" and a browser whose language is not in the supported list,
   **When** a video with no speech is processed, **Then** the output is in English and the
   no-speech notice names English.

---

### User Story 3 - A one-off language for a single video (Priority: P3)

A creator who normally uses one language needs one video's metadata in another. They switch the
choice for that upload, and switch it back afterwards. Jobs that are already running or finished
keep the language they were started with.

**Why this priority**: It is the rare case; the remembered choice from Story 1 already covers it
mechanically, this story only fixes the per-job behaviour.

**Independent Test**: Start a job with language A, switch the choice to language B and start a
second job; verify each job's output (and any regenerated section) stays in its own language.

**Acceptance Scenarios**:

1. **Given** a job was started with one language, **When** the creator changes the choice
   afterwards, **Then** that job's output and regenerations keep the original language.
2. **Given** the choice was changed, **When** the next video is uploaded, **Then** it uses the
   new choice, and the new choice is remembered on the device.

### Edge Cases

- The device cannot remember anything (private window, blocked storage): the choice still works
  for the current visit and falls back to "Same as the speech" on the next one; nothing breaks.
- A remembered language is no longer in the supported list (the list changed between releases):
  the choice falls back to "Same as the speech".
- The browser reports a regional language (for example a language plus a country): the base
  language is used if it is supported.
- A request names an unsupported or malformed language: it is refused with a readable error and
  no job is created.
- A video with an interrupted upload is resumed: it keeps the language chosen when the upload
  started, even if the choice changed in between.
- Spoken language detected with low confidence: unchanged from today when "Same as the speech" is
  selected; irrelevant when a language is picked.
- Videos shorter than 45 seconds (no chapters) follow the same language rules for the sections
  they get.

## Requirements *(mandatory)*

### Functional Requirements

- **FR-001**: The YouTube Prep upload page MUST offer a language choice with "Same as the speech"
  as the default plus a list of supported languages, each shown by its own name (its endonym) so a
  speaker of that language can find it regardless of the interface language.
- **FR-002**: The supported list MUST be a fixed, generic set of widely used languages covered by
  both transcription and text generation; it MUST NOT be tailored to any particular operator.
- **FR-003**: The chosen value MUST be remembered on the device and pre-selected on later visits;
  when it cannot be stored or is no longer supported, the choice MUST fall back to "Same as the
  speech" without an error.
- **FR-004**: The language in effect when a job is created MUST be stored with that job and MUST
  NOT change for the job's lifetime, including resumed uploads, processing and regenerations.
- **FR-005**: When a language is picked, every output section (cover titles, title, description,
  tags, hashtags, chapter titles) MUST be written in that language, whatever is spoken in the
  video.
- **FR-006**: When "Same as the speech" is in effect and speech is detected, the output MUST be in
  the detected spoken language (today's behaviour).
- **FR-007**: When "Same as the speech" is in effect and no speech is detected, the output MUST be
  in the uploading browser's language if that language (or its base language) is supported, and
  in English otherwise. This replaces the unconditional English of 001 FR-020 for silent videos.
- **FR-008**: The result page MUST always show the language the output was written in, by its own
  name, and the no-speech notice (001 FR-033) MUST name it as well.
- **FR-009**: Regenerating a section MUST produce text in the job's language.
- **FR-010**: Creating a job with an unsupported or malformed language MUST be refused with a
  readable error, and no job is created.
- **FR-011**: The language choice MUST NOT add to the cost of a job, and MUST NOT disturb the reuse
  of already-sent video content between a job's generation, repair and regeneration requests
  (001 SC-004).
- **FR-012**: The feature MUST NOT introduce any server-wide language setting; the language is a
  per-job input only.
- **FR-013**: All new interface text MUST come from the interface's message catalog, and the
  language choice MUST meet the existing mobile rules (usable at 360 px wide with no horizontal
  scroll, touch targets at least 44×44 px).

### Key Entities

- **Language choice**: either "Same as the speech" or one supported language (identified by its
  two-letter code); remembered per device; sent when a job is created.
- **Job language setting**: the choice stored with a job at creation, plus — for "Same as the
  speech" — the browser's language to fall back to when no speech is found. Fixed for the job's
  lifetime.
- **Output language**: the language the job's results are actually written in, derived from the
  job language setting and the transcription result; named in the no-speech notice.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: With a language picked, 100% of output sections for silent videos and for videos
  spoken in another language are in the picked language (checked on a sample of each).
- **SC-002**: A creator who picked a language once needs zero further steps to get that language
  on later uploads from the same device.
- **SC-003**: With "Same as the speech" left as is, results for videos with speech are unchanged
  from the previous release.
- **SC-004**: No job's cost increases because of the language choice, and regenerating a section
  keeps the language in 100% of cases.
- **SC-005**: The upload page with the language choice passes the existing mobile checks on every
  supported device profile.

## Assumptions

- The supported list is the set of languages the default transcription and text-generation
  models handle well; about 30–70 entries, ordered by their own names. The exact list is a
  planning decision.
- "Browser's language" means the browser's preferred language as reported to the page at upload
  time; VideoKit has no user accounts, so per-device memory is the only form of "set once".
- English stays the final fallback because it is the one language guaranteed in the list.
- Jobs created by an older client that sends no language behave as "Same as the speech" with
  English as the silent-video fallback (exactly today's behaviour).
- The interface itself stays English-only; only the generated output language changes.
- Out of scope: a server-wide default language, changing the language of an existing job, and
  translating the interface.
