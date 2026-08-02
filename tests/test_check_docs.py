"""The docs gate's release bookkeeping (ADR-0038).

Five releases shipped a `## [x.y.z]` section whose link definition was never
added, so the heading rendered as literal brackets and the compare link did
not exist. Nothing in the gate could see it. These tests pin the check that
now can.
"""

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check-docs.py"

COMPARE = "https://github.com/speech115/tgcli/compare"


def run(
    changelog: Path,
    *,
    readme: Path | None = None,
    project_map: Path | None = None,
    bench: Path | None = None,
    contributing: Path | None = None,
    pr_template: Path | None = None,
    adr_index: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    argv = [sys.executable, str(SCRIPT), "--changelog", str(changelog)]
    if readme is not None:
        argv.extend(["--readme", str(readme)])
    if project_map is not None:
        argv.extend(["--map", str(project_map)])
    if bench is not None:
        argv.extend(["--bench", str(bench)])
    if contributing is not None:
        argv.extend(["--contributing", str(contributing)])
    if pr_template is not None:
        argv.extend(["--pr-template", str(pr_template)])
    if adr_index is not None:
        argv.extend(["--adr-index", str(adr_index)])
    return subprocess.run(
        argv,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def changelog(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "CHANGELOG.md"
    path.write_text(body)
    return path


def copy_with_replacement(tmp_path: Path, source: Path, old: str, new: str) -> Path:
    path = tmp_path / source.name
    text = source.read_text()
    assert old in text
    path.write_text(text.replace(old, new, 1))
    return path


def test_the_repository_changelog_is_consistent():
    result = run(ROOT / "CHANGELOG.md")

    assert result.returncode == 0, result.stdout
    assert "problems: 0" in result.stdout


def test_readme_global_flags_must_include_every_parser_global(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text(
        "**Global flags:** `--account NAME`, `--json`, `--plain`, `--readonly`, "
        "`--timeout SEC`, `-v/--verbose`, `--version`.\n\n"
        "**Environment overrides:** none.\n"
    )

    result = run(ROOT / "CHANGELOG.md", readme=readme)

    assert result.returncode == 1
    assert "README.md: global flags missing --session-role" in result.stdout


def test_readme_global_flags_must_not_keep_removed_parser_flags(tmp_path):
    readme = copy_with_replacement(
        tmp_path,
        ROOT / "README.md",
        "`--version`.",
        "`--version`, `--legacy`.",
    )

    result = run(ROOT / "CHANGELOG.md", readme=readme)

    assert result.returncode == 1
    assert "README.md: unknown root global flag --legacy" in result.stdout


def test_readme_must_link_every_task_guide_page(tmp_path):
    readme = tmp_path / "README.md"
    source = (ROOT / "README.md").read_text()
    assert source.count("(docs/guide/changes.md)") >= 1
    readme.write_text(source.replace("(docs/guide/changes.md)", ""))
    result = run(ROOT / "CHANGELOG.md", readme=readme)

    assert result.returncode == 1
    assert "README.md: guide page is not linked: docs/guide/changes.md" in result.stdout


def test_readme_random_id_guarantees_are_scoped_to_send_and_forward(tmp_path):
    readme = copy_with_replacement(
        tmp_path,
        ROOT / "README.md",
        "- **Safe correspondence** — `send`, `edit`, `delete`, `forward`, and "
        "draft writes all go through preview → commit with single-use ids, a "
        "5-minute TTL, operation-specific retry checks, and an append-only "
        "audit log.",
        "- **Safe correspondence** — `send`, `edit`, `delete`, `forward`, and "
        "`draft` all use preview → commit with `random_id` retry confirmation.",
    )
    result = run(ROOT / "CHANGELOG.md", readme=readme)

    assert result.returncode == 1
    assert (
        "README.md: random_id guarantee must be scoped to send and forward"
        in result.stdout
    )


def test_readme_clone_commits_require_a_fresh_preview_after_failure(tmp_path):
    readme = copy_with_replacement(
        tmp_path,
        ROOT / "README.md",
        "If either commit fails, create a fresh preview before retrying",
        "If either commit fails, retry the same preview id",
    )
    result = run(ROOT / "CHANGELOG.md", readme=readme)

    assert result.returncode == 1
    assert (
        "README.md: clone init/refresh retries must require a fresh preview"
        in result.stdout
    )


def test_docs_cannot_claim_benchmark_coverage_the_script_does_not_have(tmp_path):
    project_map = copy_with_replacement(
        tmp_path,
        ROOT / "docs" / "MAP.md",
        "representative 13-step live smoke benchmark",
        "live benchmark: every command against a real account",
    )
    result = run(
        ROOT / "CHANGELOG.md",
        readme=ROOT / "README.md",
        project_map=project_map,
        bench=ROOT / "scripts" / "bench.py",
    )

    assert result.returncode == 1
    assert "benchmark claims every command but omits:" in result.stdout
    assert "changes" in result.stdout
    assert "clone" in result.stdout


@pytest.mark.parametrize(
    "claim",
    (
        "exhaustive command benchmark",
        "benchmark covers all commands",
    ),
)
def test_equivalent_exhaustive_benchmark_claims_are_checked(tmp_path, claim):
    project_map = copy_with_replacement(
        tmp_path,
        ROOT / "docs" / "MAP.md",
        "representative 13-step live smoke benchmark",
        claim,
    )

    result = run(ROOT / "CHANGELOG.md", project_map=project_map)

    assert result.returncode == 1
    assert "benchmark claims every command but omits:" in result.stdout


def test_every_adr_must_have_its_index_row(tmp_path):
    """AGENTS.md requires the index row in the same commit as the ADR.

    The rule carries no status carve-out — the index has a Status column so a
    not-yet-accepted ADR is listed as one. Until this check existed the rule
    held only by memory, and ADR-0072 shipped for review without its row.
    """
    index = copy_with_replacement(
        tmp_path,
        ROOT / "docs" / "decisions" / "README.md",
        "| [0072](ADR-0072-account-request-governor.md)",
        "| [0072](ADR-0072-removed-from-the-index.md)",
    )
    result = run(ROOT / "CHANGELOG.md", adr_index=index)

    assert result.returncode == 1
    assert "README.md: no index row for ADR-0072" in result.stdout


def load_check_docs():
    """`check-docs.py` is not importable by name — the hyphen forbids it."""
    spec = importlib.util.spec_from_file_location("check_docs", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def write_adr(decisions: Path, number: str, slug: str, header: str) -> None:
    path = decisions / f"ADR-{number}-{slug}.md"
    path.write_text(
        f"# ADR-{number}: {slug}\n\nDate: 2026-08-02\n{header}\n\n## Context\n"
    )


def test_a_superseded_adr_must_say_so_in_its_own_status(tmp_path):
    """AGENTS.md requires the target's Status to record the supersession.

    The index row is a separate rule with its own check; both are required in
    the same commit as the superseding ADR. This one held only by memory until
    ADR-0072 landed its supersessions in the index while ADR-0045's and
    ADR-0052's headers stayed a bare `accepted`.
    """
    decisions = tmp_path / "decisions"
    decisions.mkdir()
    write_adr(decisions, "0001", "old-rule", "Status: accepted")
    write_adr(
        decisions,
        "0002",
        "new-rule",
        "Status: accepted\nSupersedes: [ADR-0001](ADR-0001-old-rule.md) entirely.",
    )

    problems = load_check_docs().adr_supersession_problems(decisions)

    assert problems == ["ADR-0001: Status does not record being superseded by ADR-0002"]


def test_a_superseded_adr_that_records_it_passes(tmp_path):
    decisions = tmp_path / "decisions"
    decisions.mkdir()
    write_adr(
        decisions,
        "0001",
        "old-rule",
        "Status: accepted; superseded by [ADR-0002](ADR-0002-new-rule.md).",
    )
    write_adr(
        decisions,
        "0002",
        "new-rule",
        "Status: accepted\nSupersedes: [ADR-0001](ADR-0001-old-rule.md) entirely.",
    )

    assert load_check_docs().adr_supersession_problems(decisions) == []


def test_the_repository_supersessions_are_all_recorded():
    assert load_check_docs().adr_supersession_problems() == []


def test_map_inventory_counts_must_match_the_tree(tmp_path):
    project_map = tmp_path / "MAP.md"
    project_map.write_text(
        (ROOT / "docs" / "MAP.md")
        .read_text()
        .replace("task pages, 25 + index", "task pages, 22 + index", 1)
        .replace("ADR-0001…0072", "ADR-0001…0057", 1)
    )
    result = run(
        ROOT / "CHANGELOG.md",
        project_map=project_map,
    )

    assert result.returncode == 1
    assert "MAP.md: guide count is 22; tree has 25 task pages" in result.stdout
    assert "MAP.md: ADR range ends at 0057; tree ends at 0072" in result.stdout


def test_contributor_docs_must_not_send_sessions_to_closed_devlog(tmp_path):
    contributing = tmp_path / "CONTRIBUTING.md"
    contributing.write_text(
        "Anything | one entry in [docs/DEVLOG.md](docs/DEVLOG.md)\n"
    )
    pr_template = tmp_path / "PULL_REQUEST_TEMPLATE.md"
    pr_template.write_text("- [ ] `docs/DEVLOG.md` entry appended\n")
    result = run(
        ROOT / "CHANGELOG.md",
        contributing=contributing,
        pr_template=pr_template,
    )

    assert result.returncode == 1
    assert (
        "CONTRIBUTING.md: points session entries at closed docs/DEVLOG.md"
        in result.stdout
    )
    assert (
        "PULL_REQUEST_TEMPLATE.md: points session entries at closed docs/DEVLOG.md"
        in result.stdout
    )


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
