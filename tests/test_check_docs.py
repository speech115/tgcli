"""The docs gate: active docs must match the real CLI."""

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "check-docs.py"


def run(
    *,
    readme: Path | None = None,
    skill: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    argv = [sys.executable, str(SCRIPT)]
    if readme is not None:
        argv.extend(["--readme", str(readme)])
    if skill is not None:
        argv.extend(["--skill", str(skill)])
    return subprocess.run(
        argv,
        cwd=ROOT,
        capture_output=True,
        text=True,
    )


def copy_with_replacement(tmp_path: Path, source: Path, old: str, new: str) -> Path:
    path = tmp_path / source.name
    text = source.read_text()
    assert old in text
    path.write_text(text.replace(old, new, 1))
    return path


@pytest.mark.parametrize(
    ("name", "lane", "key", "role"),
    [
        ("tgcli-jobs-telegram.plist", "telegram", "archive-sync", "job"),
        ("tgcli-jobs-local.plist", "local", "archive-transcribe", None),
    ],
)
def test_jobs_plist_argv_stays_parseable(name, lane, key, role):
    """The checked-in launchd templates must keep parsing as bounded rearm."""
    import plistlib

    from tgcli.parser import build_parser

    plist_path = ROOT / "docs" / "assets" / name
    with plist_path.open("rb") as handle:
        plist = plistlib.load(handle)
    argv = plist["ProgramArguments"][1:]  # drop the absolute tg path
    args = build_parser().parse_args(argv)
    assert args.command == "jobs"
    assert args.jobs_command == "run"
    assert args.rearm == key
    assert args.lane is None
    assert getattr(args, "session_role", None) == role
    assert args.max_runtime == 3000


def test_the_repository_docs_are_consistent():
    result = run()

    assert result.returncode == 0, result.stdout
    assert "problems: 0" in result.stdout


def test_readme_global_flags_must_include_every_parser_global(tmp_path):
    readme = tmp_path / "README.md"
    readme.write_text(
        "**Global flags:** `--account NAME`, `--json`, `--plain`, `--readonly`, "
        "`--timeout SEC`, `-v/--verbose`, `--version`.\n\n"
        "**Environment overrides:** none.\n"
    )

    result = run(readme=readme)

    assert result.returncode == 1
    assert "README.md: global flags missing --session-role" in result.stdout


def test_readme_global_flags_must_not_keep_removed_parser_flags(tmp_path):
    readme = copy_with_replacement(
        tmp_path,
        ROOT / "README.md",
        "`--version`.",
        "`--version`, `--legacy`.",
    )

    result = run(readme=readme)

    assert result.returncode == 1
    assert "README.md: unknown root global flag --legacy" in result.stdout


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
    result = run(readme=readme)

    assert result.returncode == 1
    assert (
        "README.md: random_id guarantee must be scoped to send and forward"
        in result.stdout
    )


def test_readme_clone_commits_retry_the_same_preview_after_failure(tmp_path):
    readme = copy_with_replacement(
        tmp_path,
        ROOT / "README.md",
        "retry the same preview id within its TTL",
        "create a fresh preview before retrying",
    )
    result = run(readme=readme)

    assert result.returncode == 1
    assert (
        "README.md: clone init/refresh retries must reuse the same preview id"
        in result.stdout
    )


def test_a_doc_naming_a_fake_flag_command_or_link_fails(tmp_path):
    skill = tmp_path / "SKILL.md"
    skill.write_text(
        "Run `tg clone sync --no-such-flag`, then `tg no-such-command`.\n"
        "See [the old page](missing.md).\n"
    )

    result = run(skill=skill)

    assert result.returncode == 1
    assert "SKILL.md: unknown flag --no-such-flag" in result.stdout
    assert "SKILL.md: unknown command 'tg no-such-command'" in result.stdout
    assert "SKILL.md: dead link -> missing.md" in result.stdout
