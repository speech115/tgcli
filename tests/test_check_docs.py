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
    bench: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    argv = [sys.executable, str(SCRIPT)]
    if readme is not None:
        argv.extend(["--readme", str(readme)])
    if bench is not None:
        argv.extend(["--bench", str(bench)])
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


def test_readme_must_link_every_task_guide_page(tmp_path):
    readme = tmp_path / "README.md"
    source = (ROOT / "README.md").read_text()
    assert source.count("(docs/guide/changes.md)") >= 1
    readme.write_text(source.replace("(docs/guide/changes.md)", ""))
    result = run(readme=readme)

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


def test_docs_cannot_claim_benchmark_coverage_the_script_does_not_have(tmp_path):
    readme = copy_with_replacement(
        tmp_path,
        ROOT / "README.md",
        "representative 13-step live smoke benchmark",
        "live benchmark: every command against a real account",
    )
    result = run(readme=readme, bench=ROOT / "scripts" / "bench.py")

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
    readme = copy_with_replacement(
        tmp_path,
        ROOT / "README.md",
        "representative 13-step live smoke benchmark",
        claim,
    )

    result = run(readme=readme)

    assert result.returncode == 1
    assert "benchmark claims every command but omits:" in result.stdout


def test_active_glossary_does_not_define_removed_qr_login():
    glossary = (ROOT / "CONTEXT.md").read_text()

    assert "**QR login**" not in glossary
    assert "tg://login" not in glossary


def test_active_contract_and_adr_index_match_the_jobs_cutover():
    contract = (ROOT / "docs" / "CONTRACT.md").read_text()
    index = (ROOT / "docs" / "decisions" / "README.md").read_text()
    refresh = (
        ROOT / "docs" / "decisions" / "ADR-0070-archive-refresh-scheduling.md"
    ).read_text()

    assert "Schema v7 tables" in contract
    assert "`tg accounts login` requires explicit phone authorization" in index
    assert "| superseded by ADR-0087 |" in index
    assert "hourly one-shot refresh" not in index
    assert "refresh scheduling amended by ADR-0087" in index
    assert "Status: superseded by ADR-0087" in refresh
