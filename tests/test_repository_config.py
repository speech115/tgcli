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
