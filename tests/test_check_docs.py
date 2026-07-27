"""The docs gate's release bookkeeping (ADR-0038).

Five releases shipped a `## [x.y.z]` section whose link definition was never
added, so the heading rendered as literal brackets and the compare link did
not exist. Nothing in the gate could see it. These tests pin the check that
now can.
"""

import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check-docs.py"

COMPARE = "https://github.com/speech115/tgcli/compare"


def run(changelog: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--changelog", str(changelog)],
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def changelog(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "CHANGELOG.md"
    path.write_text(body)
    return path


def test_the_repository_changelog_is_consistent():
    result = run(ROOT / "CHANGELOG.md")

    assert result.returncode == 0, result.stdout
    assert "problems: 0" in result.stdout


def test_a_release_section_without_its_link_definition_fails(tmp_path):
    """The exact drift that went unnoticed for five releases."""
    result = run(
        changelog(
            tmp_path,
            f"## [1.2.16] — 2026-07-26\n\n## [1.2.15] — 2026-07-25\n\n"
            f"[1.2.15]: {COMPARE}/v1.2.14...v1.2.15\n",
        )
    )

    assert result.returncode == 1
    assert "release 1.2.16 has no [1.2.16]: link definition" in result.stdout


def test_a_link_definition_naming_another_version_fails(tmp_path):
    """A copy-pasted line points at the wrong diff while looking correct."""
    result = run(
        changelog(
            tmp_path,
            f"## [1.2.16] — 2026-07-26\n\n[1.2.16]: {COMPARE}/v1.2.14...v1.2.15\n",
        )
    )

    assert result.returncode == 1
    assert "[1.2.16]: link points at" in result.stdout
    assert "not v1.2.16" in result.stdout


def test_a_link_definition_without_a_section_fails(tmp_path):
    result = run(
        changelog(
            tmp_path,
            f"## [1.2.16] — 2026-07-26\n\n[1.2.16]: {COMPARE}/"
            f"v1.2.15...v1.2.16\n[1.2.99]: {COMPARE}/v1.2.98...v1.2.99\n",
        )
    )

    assert result.returncode == 1
    assert "[1.2.99]: link definition has no release section" in result.stdout


def test_a_version_written_twice_fails(tmp_path):
    """`CHANGELOG.md` is integrator-merged; keeping both hunks of a conflict
    duplicates a heading, and a set-based check would never see it."""
    result = run(
        changelog(
            tmp_path,
            f"## [1.2.16] — 2026-07-26\n\n## [1.2.16] — 2026-07-26\n\n"
            f"[1.2.16]: {COMPARE}/v1.2.15...v1.2.16\n",
        )
    )

    assert result.returncode == 1
    assert "release 1.2.16 has 2 sections; expected exactly one" in result.stdout


def test_a_duplicate_link_definition_fails(tmp_path):
    """The second definition silently wins in a dict, so the wrong compare
    range can outlive the right one without a single complaint."""
    result = run(
        changelog(
            tmp_path,
            f"## [1.2.16] — 2026-07-26\n\n[1.2.16]: {COMPARE}/v1.2.15...v1.2.16\n"
            f"[1.2.16]: {COMPARE}/v1.2.14...v1.2.15\n",
        )
    )

    assert result.returncode == 1
    assert "[1.2.16]: has 2 link definitions; expected exactly one" in result.stdout


def test_a_missing_changelog_fails_without_a_traceback(tmp_path):
    result = run(tmp_path / "nope.md")

    assert result.returncode == 1
    assert "no changelog at" in result.stdout
    assert "Traceback" not in result.stderr


def test_the_first_release_may_link_to_its_tag_instead_of_a_compare(tmp_path):
    """`1.0.0` has no predecessor, so it names a tag rather than a range."""
    result = run(
        changelog(
            tmp_path,
            "## [1.0.0] — 2026-07-17\n\n"
            "[1.0.0]: https://github.com/speech115/tgcli/releases/tag/v1.0.0\n",
        )
    )

    assert result.returncode == 0, result.stdout
    assert "releases checked: 1" in result.stdout


def test_an_unreleased_section_is_not_a_release(tmp_path):
    """`## [Unreleased]` carries no version and needs no compare link."""
    result = run(changelog(tmp_path, "## [Unreleased]\n\n### Added\n\n- nothing\n"))

    assert result.returncode == 0, result.stdout
    assert "releases checked: 0" in result.stdout
