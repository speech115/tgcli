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
