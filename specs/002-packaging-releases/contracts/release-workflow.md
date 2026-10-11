# Contract: Release Workflow (`.github/workflows/release.yml`)

## Trigger

`push` of a tag matching `v[0-9]+.[0-9]+.[0-9]+`. Nothing else (no branch pushes, no manual
dispatch). The workflow reads the repository and never commits, tags or pushes to it.

## Jobs

| Job | Needs | Does | Permissions |
| --- | --- | --- | --- |
| `checks` | — | full-history checkout; tag format; `check-tag-on-main.sh "$GITHUB_SHA"`; `check-release-version.sh "$GITHUB_REF_NAME"`; `extract-changelog.sh` → release notes artifact | `contents: read` |
| `ci` | — | `uses: ./.github/workflows/ci.yml` — every CI job: lint, unit tests + 80 % coverage gate, browser tests, hadolint, image build + smoke test + size guard, secret scan | as CI |
| `publish` | `checks`, `ci` | build image (`load: true`, amd64, labels from metadata) → `docker_smoke.sh` → `image_size_guard.sh` → push the same build as `X.Y.Z` and `latest` → build-provenance attestation → GitHub release `vX.Y.Z` with the notes and `docker pull ghcr.io/<owner>/<repo>:X.Y.Z` | `contents: write`, `packages: write`, `id-token: write`, `attestations: write` |

`concurrency: release-${{ github.ref }}`, `cancel-in-progress: false`.

## Guarantees

- If `checks` or `ci` fails, `publish` does not run: no image tag, no attestation, no release page.
- If the smoke test or size guard fails in `publish`, nothing is pushed.
- The release page is created last; a run that fails after pushing the image is retried by
  deleting the tag (and any half-created release page) and pushing it again.

## Changes to `ci.yml`

- Adds `workflow_call:` to `on:`; behaviour on `push` to `main` and `pull_request` is unchanged.
- The `image` job runs `scripts/image_size_guard.sh videokit:ci` after the smoke test.
