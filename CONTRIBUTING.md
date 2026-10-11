# Working on VideoKit

**Pull requests and issues are not accepted** (see the [README](README.md#about-this-project)). This
guide is for anyone working on a fork or a local checkout: how to set up, what the gate checks, and
the conventions the code follows. The full rule set for agents working on the code is in
[`.claude/CLAUDE.md`](.claude/CLAUDE.md); where the two differ, that file wins.

## Prerequisites

- [mise](https://mise.jdx.dev) installs the toolchain pinned in `.mise.toml`: Python, uv, Node,
  Task, gitleaks, hadolint, actionlint, pre-commit and git-cliff.
- [Task](https://taskfile.dev) runs the project's commands (`Taskfile.yml`).
- [pre-commit](https://pre-commit.com) runs the hooks in `.pre-commit-config.yaml`.
- [Docker](https://www.docker.com) builds and runs the image (`task docker:build`,
  `task docker:run`).
- Optional: the NVIDIA container toolkit, for running the image with GPU access
  (`task docker:run GPU=true`, `task docker:gpu-check`).

## Getting started

```bash
mise install && task setup   # pinned toolchain, then backend and frontend dependencies
pre-commit install           # commit-msg, pre-commit, pre-push and beads hooks
task lint                    # static checks
task test                    # unit and integration tests with coverage
task e2e                     # browser tests (needs Playwright browsers installed)
```

The browsers for `task e2e` come from `npx playwright install --with-deps chromium webkit`, run in
`frontend/`. `task format` auto-formats both stacks.

## The gate

`task lint` runs ruff, `ruff format --check` and strict mypy on the backend; `vue-tsc`, eslint and
prettier on the frontend; hadolint on the `Dockerfile`; and actionlint on the workflows.
`task test` runs the backend tests (pytest, coverage floor 80 %) and the frontend tests (vitest,
line coverage 80 %). The pre-push hook runs both, and both must pass before every push.

CI runs the same checks and adds the browser tests (`task e2e`), the image build with a container
smoke test and the compressed-size guard (`task docker:size`), and a gitleaks scan of the full
history. Never pass `--no-verify`.

The default suites need no API keys, no AI-provider network access and no GPU. Real-provider tests
live in `backend/tests/live/` behind the `live` marker.

## Conventions

- **Commits** follow [Conventional Commits](https://www.conventionalcommits.org) (`feat:`, `fix:`,
  `docs:`, `build:`, `ci:`, `test:`, `chore:`), enforced by the `commit-msg` hook and the commit-lint
  workflow. The changelog is generated from them, so the subject is what a user reads in the
  release notes.
- **Features** are planned with [Spec Kit](https://github.com/github/spec-kit) (specify, clarify,
  plan, tasks, analyze) and executed with [Superpowers](https://github.com/obra/superpowers), one
  test-first task at a time. The artifacts live in `specs/<NNN-name>/`, and
  `.specify/memory/constitution.md` wins every conflict.
- **Tests first.** A behavior change starts with a test that fails for the right reason, then the
  fix, then the refactor.
- **Dependencies.** A new direct dependency needs the author's approval.

## Never commit personal data

The repository is public. No tracked file, commit message or `.beads/` entry may hold personal or
environment data: real hostnames, domains, IPs, usernames, emails, local filesystem paths, tunnel
or proxy names, hardware details or secrets. Use placeholders such as `example.com`,
`<your-api-key>` and `/data`. Secrets go in environment variables only; `.env` is gitignored and
`.env.example` holds placeholders.

The pre-commit hooks scan staged changes with gitleaks and with `scripts/check_denylist.py`, which
checks a gitignored `.denylist` file (one string per line) that you fill with your own values.

## Releases

Releases are the author's act, cut by pushing a `v*` tag. The steps are in
[`docs/RELEASING.md`](docs/RELEASING.md).
