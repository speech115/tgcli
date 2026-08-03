"""`scripts/prepare-release.py` builds the release bookkeeping ADR-0038 requires.

The integrator still writes the prose; the script owns the mechanical half —
the version in both files, the section heading, the ADR/PR list, and the
`[x.y.z]:` compare link the docs gate refuses a release without.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "prepare-release.py"
CHECK_DOCS = ROOT / "scripts" / "check-docs.py"

CHANGELOG_HEAD = """# Changelog

All notable changes to tgcli.

## [1.2.3] — 2026-08-01

### Added

- Something already released.

[1.2.3]: https://github.com/speech115/tgcli/compare/v1.2.2...v1.2.3
"""


def load(path: Path, name: str):
    """The hyphenated script names are not importable by name."""
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def release():
    return load(SCRIPT, "prepare_release")


@pytest.fixture
def repo(tmp_path):
    (tmp_path / "src" / "tgcli").mkdir(parents=True)
    (tmp_path / "CHANGELOG.md").write_text(CHANGELOG_HEAD)
    (tmp_path / "pyproject.toml").write_text(
        '[project]\nname = "tgcli"\nversion = "1.2.3"\nrequires-python = ">=3.12"\n'
    )
    (tmp_path / "src" / "tgcli" / "__init__.py").write_text('__version__ = "1.2.3"\n')
    return tmp_path


def run(repo: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--repo-root", str(repo), *args],
        capture_output=True,
        text=True,
    )


def test_the_patch_digit_is_the_default_bump(release):
    assert release.next_patch("1.2.3") == "1.2.4"
    assert release.next_patch("2.0.0") == "2.0.1"
    assert release.next_patch("1.2.25") == "1.2.26"


def test_a_malformed_version_is_refused_instead_of_guessed(release):
    with pytest.raises(ValueError):
        release.next_patch("1.2")


def test_merged_prs_are_read_from_squash_subjects(release):
    lines = [
        "Add the release preparation script (ADR-0074) (#155)",
        "a local commit that never became a PR",
        "Release 1.2.3: something (#154)",
    ]
    assert release.merged_prs(lines) == [
        (155, "Add the release preparation script (ADR-0074)"),
        (154, "Release 1.2.3: something"),
    ]


def test_adrs_are_read_from_the_paths_the_slice_added(release):
    paths = [
        "docs/decisions/ADR-0074-release-preparation.md",
        "docs/decisions/README.md",
        "src/tgcli/cli.py",
    ]
    assert release.added_adrs(paths) == ["ADR-0074"]


def test_the_section_names_its_adrs_prs_and_date(release):
    section = release.render_section(
        version="1.2.4",
        date="2026-08-03",
        prs=[(155, "Add the release preparation script (ADR-0074)")],
        adrs=["ADR-0074"],
    )
    assert section.startswith("## [1.2.4] — 2026-08-03")
    assert "ADR-0074" in section
    assert "#155" in section
    assert "Add the release preparation script" in section


def test_the_compare_link_spans_the_previous_release(release):
    assert release.render_link("1.2.4", "1.2.3") == (
        "[1.2.4]: https://github.com/speech115/tgcli/compare/v1.2.3...v1.2.4"
    )


def test_the_version_moves_in_both_files(repo):
    result = run(repo, "--version", "1.2.4")

    assert result.returncode == 0, result.stderr
    assert 'version = "1.2.4"' in (repo / "pyproject.toml").read_text()
    assert (
        '__version__ = "1.2.4"' in (repo / "src" / "tgcli" / "__init__.py").read_text()
    )


def test_the_section_lands_above_the_previous_release_and_the_link_with_the_others(
    repo,
):
    run(repo, "--version", "1.2.4")

    changelog = (repo / "CHANGELOG.md").read_text()
    assert changelog.index("## [1.2.4]") < changelog.index("## [1.2.3]")
    assert changelog.index("[1.2.4]: https://") < changelog.index("[1.2.3]: https://")
    assert changelog.count("[1.2.4]: https://") == 1


def test_the_result_satisfies_the_docs_gate_release_rule(repo):
    run(repo, "--version", "1.2.4")

    check_docs = load(CHECK_DOCS, "check_docs")
    problems, checked = check_docs.release_problems(repo / "CHANGELOG.md")

    assert problems == []
    assert checked == 2


def test_a_dry_run_writes_nothing_and_prints_the_section(repo):
    before = (repo / "CHANGELOG.md").read_text()

    result = run(repo, "--version", "1.2.4", "--dry-run")

    assert result.returncode == 0
    assert (repo / "CHANGELOG.md").read_text() == before
    assert 'version = "1.2.3"' in (repo / "pyproject.toml").read_text()
    assert "## [1.2.4]" in result.stdout


def test_releasing_a_version_that_already_has_a_section_is_refused(repo):
    """The recovery path after an interrupted run, not the backwards check."""
    changelog = repo / "CHANGELOG.md"
    changelog.write_text(
        CHANGELOG_HEAD.replace(
            "## [1.2.3]",
            "## [1.2.4] — 2026-08-02\n\n### Added\n\n- Half-written.\n\n## [1.2.3]",
            1,
        )
    )
    before = changelog.read_text()

    result = run(repo, "--version", "1.2.4")

    assert result.returncode == 1
    assert "already has a section" in result.stderr
    assert changelog.read_text() == before


def test_a_version_split_across_the_two_files_is_refused_not_compounded(repo):
    """An interrupted run leaves pyproject ahead of __init__; a rerun must stop."""
    (repo / "src" / "tgcli" / "__init__.py").write_text('__version__ = "1.2.2"\n')
    before = (repo / "CHANGELOG.md").read_text()

    result = run(repo, "--version", "1.2.4")

    assert result.returncode == 1
    assert "1.2.3" in result.stderr and "1.2.2" in result.stderr
    assert (repo / "CHANGELOG.md").read_text() == before
    assert 'version = "1.2.3"' in (repo / "pyproject.toml").read_text()


def test_the_version_comes_from_the_project_table_not_another_one(repo):
    (repo / "pyproject.toml").write_text(
        '[tool.something]\nversion = "9.9.9"\n\n'
        '[project]\nname = "tgcli"\nversion = "1.2.3"\n'
    )

    result = run(repo, "--version", "1.2.4")

    assert result.returncode == 0, result.stderr
    pyproject = (repo / "pyproject.toml").read_text()
    assert 'version = "9.9.9"' in pyproject
    assert 'version = "1.2.4"' in pyproject


def test_a_missing_previous_tag_is_reported_instead_of_passing_as_an_empty_slice(repo):
    result = run(repo, "--version", "1.2.4")

    assert result.returncode == 0, result.stderr
    assert "v1.2.3" in result.stderr


def test_a_version_that_equals_the_current_one_is_refused(repo):
    result = run(repo, "--version", "1.2.3")

    assert result.returncode == 1
    assert "does not move past" in result.stderr
    assert (repo / "CHANGELOG.md").read_text() == CHANGELOG_HEAD


def test_a_version_that_would_move_backwards_is_refused(repo):
    result = run(repo, "--version", "1.1.0")

    assert result.returncode == 1
    assert (repo / "CHANGELOG.md").read_text() == CHANGELOG_HEAD
