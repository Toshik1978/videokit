"""Tests for .github/workflows/release.yml (tag-triggered release) and CI being callable from it."""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import yaml

WORKFLOWS = Path(__file__).resolve().parents[3] / ".github" / "workflows"
RELEASE = WORKFLOWS / "release.yml"
CI = WORKFLOWS / "ci.yml"

WRITE_PERMISSIONS = {
    "contents": "write",
    "packages": "write",
    "id-token": "write",
    "attestations": "write",
}


def _load(path: Path) -> dict[Any, Any]:
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    assert isinstance(data, dict)
    return data


def _on(workflow: dict[Any, Any]) -> dict[str, Any]:
    # YAML 1.1 (PyYAML) reads the bare key `on` as the boolean True.
    trigger = workflow.get("on", workflow.get(True))
    assert isinstance(trigger, dict)
    return trigger


def _jobs() -> dict[str, dict[str, Any]]:
    jobs = _load(RELEASE)["jobs"]
    assert isinstance(jobs, dict)
    return jobs


def _steps(job: str) -> list[dict[str, Any]]:
    steps = _jobs()[job]["steps"]
    assert isinstance(steps, list)
    return steps


def _index(steps: list[dict[str, Any]], predicate: Any) -> int:
    matches = [i for i, step in enumerate(steps) if predicate(step)]
    assert len(matches) == 1, matches
    return matches[0]


def _runs(fragment: str) -> Any:
    return lambda step: fragment in step.get("run", "")


def _uses(action: str) -> Any:
    return lambda step: step.get("uses", "").split("@")[0] == action


def test_trigger_is_only_a_semver_tag_push() -> None:
    assert _on(_load(RELEASE)) == {"push": {"tags": ["v[0-9]+.[0-9]+.[0-9]+"]}}


def test_concurrency_never_cancels_a_running_release() -> None:
    assert _load(RELEASE)["concurrency"] == {
        "group": "release-${{ github.ref }}",
        "cancel-in-progress": False,
    }


def test_called_ci_concurrency_group_differs_from_the_release_group() -> None:
    # A called workflow sees the caller's `github` context, so CI's own group resolves with the
    # release workflow's name. Group names are case-insensitive: equal groups would deadlock.
    release = _load(RELEASE)
    ci_group = _load(CI)["concurrency"]["group"]
    ref = "refs/tags/v1.2.3"
    called = ci_group.replace("${{ github.workflow }}", release["name"]).replace("${{ github.ref }}", ref)
    own = release["concurrency"]["group"].replace("${{ github.ref }}", ref)
    assert called.lower() != own.lower()


def test_top_level_permissions_are_read_only() -> None:
    assert _load(RELEASE)["permissions"] == {"contents": "read"}


def test_job_graph_publish_needs_checks_and_ci() -> None:
    jobs = _jobs()
    assert set(jobs) == {"checks", "ci", "publish"}
    assert "needs" not in jobs["checks"]
    assert "needs" not in jobs["ci"]
    assert sorted(jobs["publish"]["needs"]) == ["checks", "ci"]


def test_ci_job_calls_the_ci_workflow() -> None:
    assert _jobs()["ci"]["uses"] == "./.github/workflows/ci.yml"


def test_ci_job_grants_every_permission_ci_requests() -> None:
    # A called workflow's jobs cannot ask for more than the calling job grants.
    granted = _jobs()["ci"]["permissions"]
    assert granted == {"contents": "read", "pull-requests": "write"}
    ci = _load(CI)
    blocks = [ci.get("permissions", {})] + [job.get("permissions", {}) for job in ci["jobs"].values()]
    for block in blocks:
        for scope, level in block.items():
            assert granted.get(scope) == "write" or granted.get(scope) == level, (scope, level)


def test_only_publish_gets_release_write_permissions() -> None:
    jobs = _jobs()
    assert jobs["publish"]["permissions"] == WRITE_PERMISSIONS
    assert jobs["checks"]["permissions"] == {"contents": "read"}
    # The ci job's single write scope is the PR comment of CI's test job (skipped on tags).
    ci_writes = {s for s, level in jobs["ci"]["permissions"].items() if level == "write"}
    assert ci_writes == {"pull-requests"}


def test_checks_job_runs_the_guards_and_uploads_the_notes() -> None:
    steps = _steps("checks")
    checkout = steps[_index(steps, _uses("actions/checkout"))]
    assert checkout["with"]["fetch-depth"] == 0
    tag_format = steps[_index(steps, _runs('GITHUB_REF_NAME" =~'))]["run"]
    assert r"^v[0-9]+\.[0-9]+\.[0-9]+$" in tag_format
    on_main = _index(steps, _runs('check-tag-on-main.sh "$GITHUB_SHA" origin/main'))
    fetch_main = _index(steps, _runs("git fetch"))
    assert fetch_main < on_main
    _index(steps, _runs('check-release-version.sh "$GITHUB_REF_NAME"'))
    extract = _index(
        steps,
        _runs('extract-changelog.sh "$GITHUB_REF_NAME" > "$RUNNER_TEMP/release-notes.md"'),
    )
    upload = _index(steps, _uses("actions/upload-artifact"))
    assert extract < upload


def test_publish_checks_the_image_before_pushing_and_releases_after() -> None:
    steps = _steps("publish")
    build = _index(steps, lambda s: _uses("docker/build-push-action")(s) and "id" not in s)
    smoke = _index(steps, _runs("scripts/docker_smoke.sh videokit:release"))
    size = _index(steps, _runs("scripts/image_size_guard.sh videokit:release"))
    push = _index(steps, lambda s: s.get("id") == "push")
    attest = _index(steps, _uses("actions/attest"))
    release = _index(steps, _uses("softprops/action-gh-release"))
    assert build < smoke < size < push < attest < release


def test_publish_build_and_push_settings() -> None:
    steps = _steps("publish")
    meta = steps[_index(steps, _uses("docker/metadata-action"))]
    assert meta["with"]["tags"].split() == ["type=semver,pattern={{version}}", "type=raw,value=latest"]
    assert "org.opencontainers.image.title=VideoKit" in meta["with"]["labels"]

    build = steps[_index(steps, lambda s: _uses("docker/build-push-action")(s) and "id" not in s)]
    assert build["with"]["platforms"] == "linux/amd64"
    assert build["with"]["load"] is True
    assert build["with"]["tags"] == "videokit:release"

    push = steps[_index(steps, lambda s: s.get("id") == "push")]
    assert push["uses"].startswith("docker/build-push-action@")
    assert push["with"]["push"] is True
    assert push["with"]["tags"] == "${{ steps.meta.outputs.tags }}"
    assert push["with"]["labels"] == "${{ steps.meta.outputs.labels }}"
    for step in (build, push):
        assert step["with"]["cache-from"] == "type=gha"
        assert step["with"]["cache-to"].startswith("type=gha")

    attest = steps[_index(steps, _uses("actions/attest"))]
    assert attest["with"]["subject-digest"] == "${{ steps.push.outputs.digest }}"
    assert attest["with"]["push-to-registry"] is True

    release = steps[_index(steps, _uses("softprops/action-gh-release"))]
    assert release["with"]["name"] == "${{ github.ref_name }}"
    assert release["with"]["tag_name"] == "${{ github.ref_name }}"
    assert "body_path" in release["with"]


def test_release_notes_get_a_docker_pull_block() -> None:
    runs = "\n".join(step.get("run", "") for step in _steps("publish"))
    assert "### Docker" in runs
    assert "```bash" in runs
    assert "docker pull" in runs


def test_actions_are_pinned_to_major_tags() -> None:
    for path in (RELEASE, CI):
        for job in _load(path)["jobs"].values():
            for step in job.get("steps", []):
                if "uses" in step:
                    assert re.fullmatch(r"[\w.-]+/[\w./-]+@v\d+", step["uses"]), step["uses"]


def test_ci_is_callable_and_keeps_its_triggers() -> None:
    trigger = _on(_load(CI))
    assert "workflow_call" in trigger
    assert trigger["push"] == {"branches": ["main"]}
    assert "pull_request" in trigger
