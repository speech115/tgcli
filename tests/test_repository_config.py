from pathlib import Path

ROOT = Path(__file__).parents[1]


def test_feature_branch_commit_has_one_ci_event() -> None:
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()

    assert 'push:\n    branches: ["**"]\n  pull_request:' in workflow
    assert "group: ${{ github.workflow }}-${{ github.ref }}" in workflow
    assert (
        "name: ${{ github.event_name == 'pull_request' && 'test' || "
        "'branch-test' }}" in workflow
    )


def test_macos_leg_runs_the_suite_on_pull_requests() -> None:
    """ADR-0059: macOS is a first-class target (bf-03 was macOS-only) but CI
    was ubuntu-only. One PR-gated macOS job runs the test suite."""
    workflow = (ROOT / ".github/workflows/ci.yml").read_text()

    assert "test-macos:" in workflow
    assert "runs-on: macos-latest" in workflow
    assert "if: github.event_name == 'pull_request'" in workflow


def test_ci_interpreter_is_pinned_to_the_project_target() -> None:
    """Without a pin, uv resolves `requires-python = ">=3.12"` to the newest
    interpreter per platform — the first macOS leg ran 3.14 while ubuntu ran
    3.12, and three pathlib-internals monkeypatches failed there. CI must
    test the interpreter the project targets (ADR-0001), one per platform
    variable, not two variables at once."""
    assert (ROOT / ".python-version").read_text().strip() == "3.12"


def test_release_commits_are_tagged_by_ci() -> None:
    """ADR-0038's tag step is CI's, not a session's.

    Sessions cannot push `refs/tags/*` (the git proxy answers 403), which is
    how seven consecutive releases shipped untagged. If this workflow loses
    its trigger, its write permission, or its push, the rule silently stops
    being enforced again.
    """
    workflow = (ROOT / ".github/workflows/release-tag.yml").read_text()

    assert "push:\n    branches: [main]" in workflow
    assert "contents: write" in workflow
    assert 'git push origin "$tag"' in workflow


def test_the_tag_job_only_fires_when_the_push_bumped_the_version() -> None:
    """Tagging HEAD unconditionally would mistag an already-shipped release.

    `v1.2.10`-`v1.2.16` are still untagged, so a job that tagged whatever
    carries the current `__version__` would pin `v1.2.16` to the next
    unrelated merge instead of `efbb9a9`. The job must compare against the
    previous tip of `main` and no-op when the version did not move.
    """
    workflow = (ROOT / ".github/workflows/release-tag.yml").read_text()

    assert "BEFORE: ${{ github.event.before }}" in workflow
    assert 'previous=$(read_version "$BEFORE")' in workflow
    assert 'if [ "$version" = "$previous" ]; then' in workflow
    # A ref with no usable predecessor, and an unreadable one, both refuse
    # rather than guess — an empty `previous` would otherwise read as a bump.
    assert workflow.count("refusing to guess") == 2
    # A tag that fails to push must turn the run red, not print "created".
    assert "set -eu" in workflow
