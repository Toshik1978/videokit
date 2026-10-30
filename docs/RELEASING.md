# Releasing

How to cut a release of VideoKit. The release machinery (`task changelog`, `task release:verify`,
`scripts/changelog.sh`, `.cliff.toml`, `CHANGELOG.md`, `.github/scripts/` and
`.github/workflows/release.yml`) matches this document; change them together.

Tagging and pushing are the author's act. Never push with `--force` to a shared branch.

## How versions work

The git tag is the version. Nothing else in the repository carries a version number:

- `pyproject.toml` holds the placeholder `0.0.0` and is never bumped.
- The release workflow publishes the image as `ghcr.io/toshik1978/videokit:X.Y.Z` and
  `ghcr.io/toshik1978/videokit:latest`.
- `CHANGELOG.md`'s first `## vX.Y.Z` heading names the release the tree is ready to publish.

Versions follow [semver](https://semver.org). git-cliff computes the next version from the
Conventional Commit types since the last tag. Pass `TAG=` to override it, for example
`TAG=v1.0.0` when you decide to go 1.0. The first release is the computed `v0.1.0`.

## Step 0: before the first push only, sweep the whole history

The repository is public from the first push. Before it, check that no commit anywhere in the
history carries personal or environment data: any name other than the author's, an email address,
a hostname or domain, an IP, a token, a tunnel or proxy name, hardware details or a local filesystem
path. `.beads/issues.jsonl` is tracked, so every past version of it counts.

```bash
git log -p --all | grep -nE '<your-name>|<your-email>|<your-host>'
```

Replace the placeholders with the identifiers from your own environment, one alternative per
identifier, and read every hit. Once the history is public this step cannot be undone, which is why
it comes before anything else.

Then create the GitHub repository, add the `GIST_ID` and `GIST_TOKEN` secrets the coverage badges
need (see `.github/workflows/ci.yml`), and push `main`.

## Step 1: generate the changelog entry

```bash
task changelog              # next version computed from the commits
task changelog TAG=v1.0.0   # or name the version yourself
```

git-cliff prepends a `## vX.Y.Z — <date>` section to `CHANGELOG.md`, listing the commits since the
last tag grouped as Features, Bug Fixes and Others. `chore`, `test`, `style`, `build(deps)` and
`ci(deps)` commits are left out. The script prints the version it wrote.

When no commit since the last tag is releasable, the script refuses with
`nothing to release since <tag>` and leaves `CHANGELOG.md` untouched.

Write a short paragraph above the generated groups: what the release means for someone running
VideoKit, not a restatement of the commit list. Mention anything an existing user must do, such as
an environment variable that changed.

For a breaking release, link the upgrade steps from that paragraph. The 0.4.0 section links
[Upgrading from 0.3](../README.md#upgrading-from-03).

## Step 2: verify, commit and tag

Check first (optional):

```bash
.github/scripts/extract-changelog.sh vX.Y.Z   # preview the exact release body
task lint && task test                        # the gate, which the pre-push hook runs anyway
```

The preview prints the section from `## vX.Y.Z` to the next `## ` heading. It exits non-zero when
the section is missing or empty, which is the guard against publishing a release with empty notes.

Then be on `main`, up to date with the remote. Five commands, in order. Replace `vX.Y.Z` with the
version `task changelog` printed.

```bash
task release:verify TAG=vX.Y.Z
git commit -m "chore(release): vX.Y.Z" CHANGELOG.md
git push
git tag -a vX.Y.Z -m vX.Y.Z
git push origin vX.Y.Z
```

`task release:verify` runs the same two guards the workflow runs: the tag is the newest changelog
version, and its section extracts with content. The `chore(release)` subject keeps the commit out
of the next release's notes. The pre-push hook runs `task lint` and `task test` on the first push,
so the gate needs no separate run.

This commit is the one change pushed straight to `main` rather than through a pull request: the
constitution's Development Workflow allows exactly this `chore(release): vX.Y.Z` changelog commit
and nothing else.

The tag must be on `main`. Pushing a tag that matches `vX.Y.Z` starts the **Publish release**
workflow (`.github/workflows/release.yml`). It never commits, tags or pushes to the repository. Its
jobs run in this order:

1. `checks` verifies the tag format, that the tagged commit is on `main`
   (`.github/scripts/check-tag-on-main.sh`), that the tag is the newest changelog version
   (`.github/scripts/check-release-version.sh`), and extracts the release notes
   (`.github/scripts/extract-changelog.sh`).
2. `ci` reruns the full CI workflow (`.github/workflows/ci.yml`) on the tagged commit: lint, tests
   with the coverage gate, browser tests, hadolint, image build with smoke test and size guard,
   and the secret scan.
3. `publish` runs only when `checks` and `ci` both pass. It builds the image, runs
   `scripts/docker_smoke.sh` and `scripts/image_size_guard.sh` on that exact build, and only then
   pushes it as `X.Y.Z` and `latest`. It then attests the build provenance and creates the GitHub
   release with the changelog section and the `docker pull` command, last of all.

If any guard or CI job fails, nothing is published: no image tag, no attestation and no release
page. If the smoke test or the size guard fails, nothing is pushed.

## After the first release

A new ghcr.io package is private by default. Open the `videokit` package on GitHub, then
Package settings, then Change visibility, and make it public so `docker pull` works without a
login. This is needed once.

## Retrying a failed release

If the workflow fails after the tag is pushed, fix the cause on `main`, then delete the tag and
push it again:

```bash
git tag -d vX.Y.Z
git push origin :refs/tags/vX.Y.Z
git tag -a vX.Y.Z -m vX.Y.Z
git push origin vX.Y.Z
```

If a release page already exists for the tag, delete it first with `gh release delete vX.Y.Z`. The
workflow creates the page as its very last step, so this only matters for a page left by a manual
or partial run. If the fix changes the changelog section, regenerate or edit it and commit it to
`main` as another `chore(release): vX.Y.Z` commit before re-tagging.
Delete and recreate the tag rather than force-pushing it: a force-push of a tag that still points
at the same commit triggers nothing.
