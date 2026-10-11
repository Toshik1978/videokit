<!--
Sync Impact Report
Version change: 1.2.0 -> 1.3.0 (MINOR: materially expanded guidance)
Modified principles: none
Modified sections: Development Workflow (the `chore(release): vX.Y.Z` changelog commit is the
  single change the author may push directly to main; everything else still goes through a pull
  request)
Added sections: none
Removed sections: none
Templates checked: .specify/templates/plan-template.md, spec-template.md, tasks-template.md,
  checklist-template.md, constitution-template.md (no changes needed)
Follow-up TODOs: none
Previous amendment: 1.1.0 -> 1.2.0 (MINOR) changed Principle I (author-name and
  repository-address exceptions) and Technology & Operational Constraints (single slim image,
  example compose.yaml) and adopted the display name VideoKit.
-->

# VideoKit Constitution

## Core Principles

### I. Open-Source Hygiene (NON-NEGOTIABLE)

VideoKit is developed in a public repository. Every artifact — source code, docs, specs, plans,
task trackers (including beads), commit messages, examples, fixtures and test data — MUST be free
of personal or environment-specific data.

- MUST NOT contain real hostnames, domains, site names, IP addresses, usernames other than the author's, email addresses,
  API keys, tokens, file paths from a developer machine, or hardware/host specifics.
- The author's name, Anton Krivenko, MAY appear where authorship is stated: LICENSE, package
  metadata, image labels and documentation bylines. The repository's own public addresses
  (`github.com/toshik1978/videokit`, `ghcr.io/toshik1978/videokit`) MAY appear in links and
  configuration. Nothing else about the author (email, other accounts, hosts, hardware) may appear.
- Examples MUST use neutral placeholders (`example.com`, `<your-api-key>`, `/data`).
- Secrets MUST be supplied only through environment variables. `.env` MUST be gitignored;
  `.env.example` MUST contain placeholders only.
- The project is licensed under MIT.

Rationale: a single leaked secret or personal detail in history is effectively permanent.

### II. Simplicity First

The system MUST stay small enough for one person to develop and operate.

- One deployable unit: a single Docker container.
- One backend language (Python) and one frontend framework (Vue 3 + Vite + TypeScript); the
  frontend is built into static assets served by the backend — no Node.js at runtime.
- No external infrastructure (message brokers, job queues, external databases). Background work
  uses an in-process queue; state lives on local disk (SQLite and/or files).
- New infrastructure, dependencies or abstractions MUST be justified by a current, documented need
  (YAGNI). Speculative generality is rejected in review.

Rationale: maintenance cost, not feature count, is the limiting factor of a hobby-scale OSS tool.

### III. Multi-Tool Modularity

VideoKit is a toolbox; each tool is a self-contained module on top of a shared core.

- `core` owns cross-cutting capabilities: uploads, jobs and queueing, media processing (ffmpeg),
  transcription, LLM access, configuration.
- Each tool lives under `tools/<tool_id>` (backend) with a matching frontend route, and registers
  itself with the tool registry.
- Adding or removing a tool MUST NOT require changes to `core`. Tools MUST use `core` only through
  its public interfaces, never its internals.
- Deterministic tool logic (formatting, validation, limits) lives in code inside the tool, not in
  prompts.

Rationale: the first tool must not shape the platform so tightly that the second one is a rewrite.

### IV. Provider Abstraction with Fallback

External AI capabilities are pluggable and degrade gracefully.

- LLM providers (Anthropic, OpenAI) and transcription providers (local Whisper, OpenAI) MUST sit
  behind provider interfaces. Tools never import a vendor SDK directly.
- Providers are configured as an ordered list (primary, then fallbacks). On a provider failure the
  next one is tried; the provider actually used is recorded with the job.
- A GPU MUST be optional: the app MUST run on CPU-only hosts and in cloud-only mode.
- Startup MUST validate that each configured provider is usable and fail fast with a clear
  message otherwise.

Rationale: operators have different hardware, budgets and vendor preferences.

### V. Test-First (NON-NEGOTIABLE)

- All production code follows TDD: write a failing test, make it pass, refactor.
- The default test suite and CI MUST run without API keys, network access to AI providers, or a
  GPU, using fake providers and small generated media fixtures.
- Tests against real providers are opt-in (explicit marker/flag) and skipped by default.
- Frontend logic is covered by component tests; type-checking and linting MUST pass.

Rationale: AI output is non-deterministic; everything around it must be deterministic and proven.

### VI. Privacy & Data Lifecycle

- Uploaded source media MUST be deleted as soon as the pipeline no longer needs it.
- Derived job artifacts MUST be purged after a configurable TTL; abandoned uploads likewise.
- Logs and UI MUST NOT expose API keys, uploaded content, transcripts, or stack traces; errors
  shown to users are concise and actionable.
- Media is sent to third-party providers only when that provider is configured by the operator.

Rationale: users upload personal footage; the app keeps it only as long as strictly necessary.

### VII. Deployment-Agnostic Security Boundary

- VideoKit ships without authentication by design. Protecting the endpoint (reverse proxy, tunnel,
  auth gateway) is the operator's responsibility and MUST be stated prominently in the README.
- The app MUST work behind proxies and tunnels that limit request body size: large uploads use
  resumable, chunked transfer.
- The app MUST NOT assume a specific host, domain, or network topology.

Rationale: one honest boundary beats a half-built auth system.

## Technology & Operational Constraints

- Backend: Python 3.13+, FastAPI, dependency management with `uv`.
- Frontend: Vue 3, Vite, TypeScript; minimal CSS, no heavy UI framework.
- Media: ffmpeg/ffprobe invoked as subprocesses.
- Transcription: local `faster-whisper` (CUDA when available, else CPU) and OpenAI transcription.
- LLM: Anthropic (default) and OpenAI, via their official SDKs.
- Packaging: one multi-stage Dockerfile building a single image for CPU and GPU hosts on a slim
  Python base that carries only the GPU libraries the transcription engine loads; an example
  `compose.yaml` that runs the published image with GPU access enabled by default.
- Configuration: environment variables only, documented in `.env.example` and the README.

## Development Workflow

- Work is specified and planned with Spec Kit (`spec.md` → `plan.md` → `tasks.md`) before
  implementation; `tasks.md` is the implementation contract.
- Every change goes through a pull request; CI (lint, format check, type-check, tests, coverage
  gate, Docker build, secret scan) MUST pass. The single exception is the release's
  `chore(release): vX.Y.Z` commit, which only adds the new section to `CHANGELOG.md`: the author
  MAY push it directly to `main` before tagging (the pre-push hook still runs lint and tests, and
  the release workflow reruns the full CI on the tagged commit).
- Linters, formatters and type-checkers are enforced from the first commit; line coverage MUST stay
  at or above 80 % for each stack (backend, frontend).
- Commit messages MUST follow Conventional Commits and Principle I; enforced locally by pre-commit
  hooks and in CI.
- Reviews MUST check: constitution compliance (especially Principle I), test coverage of new
  behavior, and absence of unjustified complexity.
- Dependencies are kept current via automated update PRs; lockfiles are committed.

## Governance

This constitution supersedes all other project practices. Amendments are made by pull request
that states the rationale and updates the version:

- MAJOR: removal or backward-incompatible redefinition of a principle.
- MINOR: a new principle or section, or materially expanded guidance.
- PATCH: clarifications and wording fixes.

Every plan MUST include a Constitution Check; any deviation MUST be recorded with justification in
the plan's Complexity Tracking. Reviewers verify compliance on every pull request.

**Version**: 1.3.0 | **Ratified**: 2026-10-11 | **Last Amended**: 2026-10-11
