# Feature Specification: Slim Image and Release Flow

**Feature Branch**: `002-packaging-releases`

**Created**: 2026-10-11

**Status**: Draft

**Input**: User description: "Packaging and releases for VideoKit: a slim single image that runs on
CPU and GPU hosts (only the GPU libraries the transcription engine needs, no general GPU toolkit
base), a size guard, a quick local GPU check, a tag-driven release flow that publishes the image
and release notes only after every check passes, the git tag as the only version, a compose file
that pulls the published image with GPU enabled, CONTRIBUTING and README notes that the project is
shared as is without support, the author's name in the license and package metadata, and
'VideoKit' as the display name."

## Clarifications

### Session 2026-10-11

- Q: Before publishing, should the release also run the browser tests on desktop and mobile, or
  only lint and unit tests? → A: Everything CI runs: lint, unit tests, browser tests, image build,
  smoke test and size check.
- Q: Should a release refuse a tag that points at a commit not on the main branch? → A: Yes; the
  tagged commit must be on main, otherwise nothing is published.
- Q: Should the published image be pullable by anyone without logging in to the registry? → A:
  Yes, public; the release guide covers making the package public once.

## User Scenarios & Testing *(mandatory)*

The **author** builds and runs VideoKit on their own server, which has an NVIDIA GPU, and publishes
the project openly. A **fork user** is anyone who clones or pulls VideoKit and runs or changes it on
their own, without support from the author.

### User Story 1 - Run a normally sized image on CPU and GPU hosts (Priority: P1)

The author builds the VideoKit image and runs it on their GPU server. Today the image is about
3.8 GB to download and 6.5 GB on disk, most of it general-purpose GPU libraries VideoKit never
uses. After this change the same single image is about a third of that size, local transcription
still runs on the GPU when one is passed through, and the image still runs on a host without a GPU,
where local transcription uses the CPU.

**Why this priority**: The oversized image is the problem that started this feature; every other
story publishes or documents this image.

**Independent Test**: Build the image, measure its compressed size, run the existing smoke test
(CPU), and run the local GPU check on a GPU host.

**Acceptance Scenarios**:

1. **Given** the image is built, **When** its compressed (download) size is measured, **Then** it
   is at most 1.25 GB.
2. **Given** a host without a GPU, **When** the image runs, **Then** the app starts, reports
   healthy, and local transcription works on the CPU.
3. **Given** a host with an NVIDIA GPU and the NVIDIA container runtime, **When** the author runs
   the local GPU check, **Then** a short generated clip is transcribed on the GPU inside the image
   and the check reports success within seconds.
4. **Given** the image runs as the host user with a bind-mounted data folder, **When** it starts
   and processes a job, **Then** it writes only under the data folder, exactly as before.
5. **Given** a change makes the image grow past the size limit, **When** CI builds it, **Then** the
   build fails and reports the measured size and the limit.

---

### User Story 2 - Cut a release (Priority: P1)

The author decides the main branch is ready. They run one command that writes the next version's
section of the changelog from the commit history and picks the next version number. They add a
short summary paragraph, commit it, push, and push the version tag. Without further action, the
image is checked, published to the container registry under that version and as `latest`, and a
release page appears with the changelog section and the command to pull the image. If any check
fails, nothing is published.

**Why this priority**: There is no way to publish VideoKit today; without it the slim image only
exists on the author's machine.

**Independent Test**: In a scratch copy of the repository, run the changelog command and confirm
the next version and section; run the release checks locally against a matching and a mismatching
tag; in the real repository, push a first tag and confirm the published image and release page.

**Acceptance Scenarios**:

1. **Given** commits since the last release, **When** the author runs the changelog command
   without naming a version, **Then** a new section headed with the computed next version is
   prepended to the changelog, grouping the user-facing commits.
2. **Given** no release exists yet, **When** the changelog command runs, **Then** the computed
   version is `v0.1.0`.
3. **Given** the author names a version explicitly, **When** the changelog command runs, **Then**
   that version is used instead of the computed one.
4. **Given** a pushed tag `vX.Y.Z` that matches the newest changelog entry on a commit whose lint
   and tests pass, **When** the release runs, **Then** the image is published as `X.Y.Z` and
   `latest` with a build-provenance attestation, and a release named `vX.Y.Z` is created whose
   notes are that changelog section plus the pull command.
5. **Given** a pushed tag that does not match the newest changelog entry, **When** the release
   runs, **Then** it stops with a message naming both versions and publishes nothing.
6. **Given** a pushed tag on a commit whose lint, unit tests, browser tests, image smoke test or
   size guard fails,
   **When** the release runs, **Then** it stops and publishes nothing.
7. **Given** a release failed after the tag was pushed, **When** the author fixes the cause and
   re-pushes the tag as the release guide describes, **Then** the release runs again from the
   start.

---

### User Story 3 - Run the published image (Priority: P2)

The author (or a fork user) creates the data folder and starts VideoKit with the provided compose
file. It pulls the latest published image instead of building it, and passes the GPU through by
default. Someone without an NVIDIA GPU removes the GPU lines, as the README explains, and the same
image runs on the CPU. Local builds remain available for development.

**Why this priority**: It is how the published image gets used, but it depends on Stories 1 and 2.

**Independent Test**: With a release published, run the documented compose start on the GPU host
and confirm the app is healthy and uses the GPU; remove the GPU lines on a CPU-only host and
confirm it starts.

**Acceptance Scenarios**:

1. **Given** a published release, **When** the operator starts VideoKit with the compose file,
   **Then** the latest published image is pulled and started with GPU access, as the host user,
   with the data folder mounted.
2. **Given** a host without the NVIDIA container runtime, **When** the operator removes the GPU
   lines as documented and starts the compose file, **Then** VideoKit starts and runs on the CPU.
3. **Given** a developer checkout, **When** the developer runs the local build and run commands,
   **Then** the image is built from source and run with the data folder mounted, as today.

---

### User Story 4 - Understand what the project is and who maintains it (Priority: P3)

A fork user opens the repository. The README says up front that VideoKit was built by AI agents,
is shared as is, and comes with no support; CONTRIBUTING explains that pull requests and issues
are not accepted and how to work on a fork. The license and package metadata name the author. The
app, README and docs call the product "VideoKit".

**Why this priority**: It sets expectations for a public repository but does not change what the
software does.

**Independent Test**: Read the README, CONTRIBUTING, LICENSE and package metadata; open the app and
check its title; search the docs and UI for the product name.

**Acceptance Scenarios**:

1. **Given** the README, **When** a visitor reads it, **Then** an "About this project" section
   states that VideoKit was built by AI agents using Spec Kit and Superpowers, is shared as is,
   and comes without support, issues or pull requests.
2. **Given** CONTRIBUTING, **When** a fork user follows it, **Then** they can install the
   toolchain, set up the hooks and run the full gate green without other guidance, and they learn
   the conventions, the never-commit-personal-data rule and where the release steps are.
3. **Given** the license and package metadata, **When** they are read, **Then** they name the
   author, Anton Krivenko.
4. **Given** the running app, **When** its title appears in the browser tab or on the home screen,
   **Then** it reads "VideoKit".

---

### Edge Cases

- A tag that is not of the form `vMAJOR.MINOR.PATCH` (for example `v1.2` or `v1.2.3-rc1`) does
  not start a release.
- A version tag is pushed on a commit that is not on the main branch: the release stops with a
  message saying so and publishes nothing.
- The changelog command finds no user-facing commits since the last release: it reports that there
  is nothing to release and leaves the changelog unchanged.
- A release fails after the image was pushed but before the release page was created: re-running
  the release replaces the image tags and creates the page; the guide covers deleting a
  half-created release page before retrying.
- The operator starts the compose file on a host without the NVIDIA container runtime: Docker
  refuses to start the container with its own error; the README tells them to remove the GPU
  lines.
- The host has a GPU but its driver is too old for the GPU libraries in the image: the local GPU
  check fails with a clear message; the app itself behaves as in feature 001 when a transcription
  provider fails (fallback to the next configured provider).
- The local GPU check runs on a host without a GPU: it fails with a message saying no GPU is
  visible to the container, not with a stack trace.
- The first run of the local GPU check needs to download the small speech model: it caches the
  model in the data folder, so later runs make no download.

## Requirements *(mandatory)*

### Functional Requirements

**Image**

- **FR-001**: The project MUST ship one image that runs on hosts with and without an NVIDIA GPU;
  with a GPU passed through, local transcription MUST use it, and without one it MUST use the CPU.
- **FR-002**: The image MUST contain only the GPU libraries the local transcription engine actually
  loads (verified on a GPU host: its matrix-math library for the supported GPU platform generation),
  pinned like every other dependency. It MUST NOT be based on a general-purpose GPU toolkit image
  and MUST NOT carry GPU libraries the engine never loads.
- **FR-003**: The image MUST NOT contain package-manager download caches or other build leftovers.
- **FR-004**: The image's compressed size for amd64 MUST NOT exceed 1.25 GB. The limit is defined in
  one place, and every CI image build and every release MUST fail when it is exceeded, reporting
  the measured size and the limit.
- **FR-005**: The image MUST keep the behaviour feature 001 established: it runs under any user id
  with the application read-only, writes only under the data folder (including model and other
  caches), and passes the existing smoke test for both the default user and the host user with a
  bind-mounted data folder.
- **FR-006**: The media tool in the image MUST come from the base distribution's packages, so that
  rebuilding on a newer base picks up its security updates, and it MUST accept at least H.264,
  HEVC, VP9 and AV1 video with AAC, Opus or MP3 audio.
- **FR-007**: The image MUST be built for amd64 only.

**Local GPU check**

- **FR-008**: The project MUST provide a command that, on a GPU host, runs the built image with the
  GPU passed through, transcribes about 5 seconds of generated audio on the GPU with the smallest
  local speech model, and exits successfully only if the transcription ran on the GPU.
- **FR-009**: The local GPU check MUST cache the model in the local data folder, MUST fail with a
  one-line reason (no GPU visible, GPU libraries failed to load) instead of a stack trace, and is
  not part of CI.

**Releases**

- **FR-010**: The project MUST provide a changelog command that prepends the next release's section
  to the changelog from the commit history, computing the next version from the commit types; an
  optional explicit version overrides the computed one, and the first release is `v0.1.0`.
  Changelog sections MUST group commits into features, fixes and others, and MUST leave out
  release commits, chores, tests, style changes and automated dependency updates.
- **FR-011**: A release MUST start only when a tag of the form `vMAJOR.MINOR.PATCH` is pushed, MUST
  publish nothing unless the tagged commit is on the main branch, and the release automation MUST
  NOT commit to, tag or push the repository.
- **FR-012**: Before publishing anything, a release MUST run on the tagged commit every check CI
  runs — lint, unit tests, browser tests, image build, smoke test and size guard — and check that
  the tag equals the newest changelog entry. If any of these fails, the release MUST publish
  nothing.
- **FR-013**: A release MUST publish the image to the repository's GitHub container registry as
  `X.Y.Z` and `latest`, with a build-provenance attestation and image labels naming the source
  repository, license, author, description and version. The published image MUST be pullable by
  anyone without logging in to the registry.
- **FR-014**: A release MUST create a release page named `vX.Y.Z` whose notes are that version's
  changelog section followed by the command to pull the image; a release whose changelog section
  is missing or empty MUST stop before publishing.
- **FR-015**: A release guide in `docs/RELEASING.md` MUST describe, in order: a one-time sweep of
  the whole history for personal data before the repository is first made public, generating and
  editing the changelog entry, previewing the release notes, committing and pushing, tagging, what
  the automation does, making the published image public after the first release if the registry
  created it private, and how to retry a failed release.

**Version**

- **FR-016**: The git tag MUST be the only version. No other file may carry a release version;
  where a packaging format requires a version field it MUST hold a fixed placeholder with a
  comment saying the tag is the version. Showing the version inside the app is out of scope.

**Running the published image**

- **FR-017**: The compose file MUST run the latest published image instead of building it, with
  GPU access enabled by default, as the host user, with the local data folder mounted; the README
  MUST explain removing the GPU lines on hosts without the NVIDIA container runtime.
- **FR-018**: The local build and run commands MUST keep building the image from source and running
  it with the local data folder mounted.

**Project identity and documentation**

- **FR-019**: The README MUST contain an "About this project" section stating that VideoKit was
  built by AI agents using Spec Kit and Superpowers, is shared as is, and that issues and pull
  requests are not accepted and no support is provided.
- **FR-020**: A `CONTRIBUTING.md` MUST state that pull requests and issues are not accepted and
  guide fork users through prerequisites, getting started, the gate, conventions, the rule never
  to commit personal data, and where the release steps are.
- **FR-021**: The license, the package author metadata and the image author label MUST name the
  author, Anton Krivenko.
- **FR-022**: The project's no-personal-data rule (the constitution and the guide for AI agents)
  MUST explicitly allow the author's name in authorship places and the repository's own public
  addresses (its GitHub and container-registry paths) in links and configuration, and MUST keep
  forbidding hostnames,
  domains, site names, IP addresses, other usernames, email addresses, secrets, developer-machine
  paths and hardware details. The constitution's packaging constraint MUST be updated to match
  this feature.
- **FR-023**: The display name MUST be "VideoKit" in the app's title (browser tab and home-screen
  name), the README, the new docs and the guide for AI agents. The lowercase identifier `videokit`
  MUST stay for the repository, image, Python package and commands. Feature 001's spec artifacts
  stay as written.

### Key Entities

- **Release**: one published version, identified by its tag `vX.Y.Z`; consists of the changelog
  section, the image tags `X.Y.Z` and `latest`, the provenance attestation and the release page.
- **Changelog**: the file listing every release's section, newest first; its newest section names
  the version the tree is ready to release.
- **Image**: the single container image for CPU and GPU hosts; has a compressed size checked
  against the limit and labels naming source, license, author, description and version.

## Success Criteria *(mandatory)*

### Measurable Outcomes

- **SC-001**: The image download is at most 1.25 GB, at least two thirds smaller than the 3.8 GB
  it is today.
- **SC-002**: On the author's GPU host, the local GPU check passes in under 10 seconds once the
  model is cached; on a host without a GPU the same image passes the smoke test.
- **SC-003**: After writing the summary paragraph, the author cuts a release with the documented
  steps (at most six commands), and the image and release page are published within 30 minutes of pushing the tag.
- **SC-004**: Every failure path of the release (tag/changelog mismatch, tag off main, failing lint, unit or
  browser tests, failing smoke test, size over the limit, missing changelog section) publishes
  nothing.
- **SC-005**: A search of the repository finds no release version outside the changelog and tags,
  and no lowercase "videokit" used as the display name in the UI, README or new docs.
- **SC-006**: A fork user can go from a fresh clone to a green gate by following CONTRIBUTING alone.

## Assumptions

- The repository is hosted on GitHub and releases use GitHub Actions, the GitHub container registry
  and GitHub release pages, as in the author's other projects.
- The author's GPU host has an NVIDIA driver that supports the GPU platform generation the local
  transcription engine is built for, and the NVIDIA container runtime is installed.
- One line of releases: `latest` always points at the most recent release; older versions get no
  patches, and there is no upgrade guidance or compatibility promise.
- The size limit is measured on the compressed amd64 image, which is what a host downloads.
- The release automation reuses the project's existing lint, test and smoke-test commands rather
  than defining its own.
- Switching the issue tracker off on GitHub is a repository setting the author changes by hand;
  the docs only state the policy.
- Out of scope: separate CPU and GPU images, arm64 images, showing the version in the app,
  publishing the backend or frontend as packages, and reading the published release back after it
  is created.
