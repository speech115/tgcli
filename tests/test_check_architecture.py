from __future__ import annotations

import runpy
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "check-architecture.py"
CEILINGS = {
    "src/tgcli/cli.py": 749,
    "src/tgcli/parser.py": 726,
    "src/tgcli/preflight.py": 446,
    "src/tgcli/dispatch.py": 373,
    "src/tgcli/commands/batch.py": 100,
    "src/tgcli/commands/changes.py": 556,
    "src/tgcli/read_ops.py": 474,
    "src/tgcli/commands/clone.py": 1060,
    "src/tgcli/clone/state.py": 485,
    "src/tgcli/clone/quotes.py": 392,
    "src/tgcli/clone/quote_fallback.py": 127,
    "src/tgcli/clone/send.py": 290,
    "src/tgcli/archive/store.py": 1073,
    "src/tgcli/archive/sync.py": 615,
    "src/tgcli/archive/backfill.py": 320,
    "src/tgcli/archive/transcribe.py": 251,
    "src/tgcli/archive/explore.py": 578,
    "src/tgcli/archive/search.py": 78,
    "src/tgcli/archive/media.py": 72,
    "src/tgcli/commands/archive.py": 544,
    "src/tgcli/commands/archive_jobs.py": 140,
    "src/tgcli/commands/jobs.py": 163,
    "src/tgcli/jobs/arguments.py": 79,
    "src/tgcli/jobs/model.py": 95,
    "src/tgcli/jobs/preflight.py": 108,
    "src/tgcli/jobs/db.py": 280,
    "src/tgcli/jobs/runner.py": 490,
    "src/tgcli/jobs/store.py": 665,
    "src/tgcli/governor/__init__.py": 14,
    "src/tgcli/governor/gate.py": 227,
    "src/tgcli/governor/ledger.py": 469,
    "src/tgcli/governor/pacing.py": 231,
    "src/tgcli/governor/probe.py": 86,
    "src/tgcli/governor/registry.py": 133,
    "src/tgcli/governor/seam.py": 66,
    "src/tgcli/commands/store.py": 537,
}


def _run(root: Path, *extra: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root), *extra],
        capture_output=True,
        text=True,
        check=False,
    )


def _write_minimal_tree(
    root: Path,
    *,
    cli_import: str = "",
    batch_import: str = "from tgcli import read_ops\n",
    dispatch_import: str = "",
) -> None:
    prefixes = dict.fromkeys(CEILINGS, "")
    prefixes["src/tgcli/cli.py"] = cli_import
    prefixes["src/tgcli/commands/batch.py"] = batch_import
    prefixes["src/tgcli/dispatch.py"] = dispatch_import
    for relative, prefix in prefixes.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        prefix_lines = prefix.splitlines()
        padding = ["#"] * (CEILINGS[relative] - len(prefix_lines))
        path.write_text("\n".join([*prefix_lines, *padding]) + "\n")


def test_architecture_check_rejects_read_dispatch_leaking_into_cli(tmp_path):
    _write_minimal_tree(
        tmp_path,
        cli_import="from tgcli.commands import dialogs as dialogs_cmd\n",
    )

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "src/tgcli/cli.py imports read command module dialogs" in result.stdout


def test_architecture_check_rejects_direct_read_symbol_import(tmp_path):
    _write_minimal_tree(
        tmp_path,
        cli_import="from tgcli.commands.dialogs import fetch_dialogs\n",
    )

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "src/tgcli/cli.py imports read command module dialogs" in result.stdout


def test_architecture_check_rejects_relative_read_symbol_imports(tmp_path):
    _write_minimal_tree(
        tmp_path,
        cli_import="from .commands.dialogs import fetch_dialogs\n",
        batch_import="from .dialogs import fetch_dialogs\n",
    )

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "src/tgcli/cli.py imports read command module dialogs" in result.stdout
    assert (
        "src/tgcli/commands/batch.py imports read command module dialogs"
        in result.stdout
    )


def test_architecture_check_rejects_media_manifest_dispatch(tmp_path):
    _write_minimal_tree(
        tmp_path,
        cli_import=(
            "from tgcli.commands import media as media_cmd\n"
            "media_cmd.manifest(None, None)\n"
        ),
    )

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "src/tgcli/cli.py dispatches shared read media.manifest" in result.stdout


def test_architecture_check_rejects_media_module_in_batch(tmp_path):
    _write_minimal_tree(
        tmp_path,
        batch_import="from tgcli.commands import media as media_cmd\n",
    )

    result = _run(tmp_path)

    assert result.returncode == 1
    assert (
        "src/tgcli/commands/batch.py imports read command module media" in result.stdout
    )


def test_architecture_check_accepts_owned_read_operation_seam(tmp_path):
    _write_minimal_tree(tmp_path)

    result = _run(tmp_path)

    assert result.returncode == 0
    assert result.stdout == "architecture check passed\n"


def test_fixture_ceilings_match_the_checker():
    checker = runpy.run_path(str(SCRIPT))
    assert CEILINGS == checker["CEILINGS"]
    assert checker["GRACE"] == 50


def test_architecture_check_rejects_read_dispatch_leaking_into_dispatch(tmp_path):
    _write_minimal_tree(
        tmp_path,
        dispatch_import="from tgcli.commands import search as search_cmd\n",
    )

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "src/tgcli/dispatch.py imports read command module search" in result.stdout


def test_architecture_check_accepts_a_shrunk_file(tmp_path):
    _write_minimal_tree(tmp_path)
    cli = tmp_path / "src/tgcli/cli.py"
    cli.write_text("\n".join(cli.read_text().splitlines()[:-1]) + "\n")

    result = _run(tmp_path)

    assert result.returncode == 0, result.stdout


def test_growth_within_grace_passes_with_warning(tmp_path):
    """ADR-0058: growth within the grace band passes so feature branches
    never edit ceilings; the integrator ratchets them at merge."""
    _write_minimal_tree(tmp_path)
    cli = tmp_path / "src/tgcli/cli.py"
    cli.write_text(cli.read_text() + "#\n")

    result = _run(tmp_path)

    assert result.returncode == 0, result.stdout
    assert "architecture check passed" in result.stdout
    assert "src/tgcli/cli.py has 750 lines; over ceiling 749" in result.stderr
    assert "grace" in result.stderr


def test_growth_at_the_grace_boundary_passes(tmp_path):
    _write_minimal_tree(tmp_path)
    cli = tmp_path / "src/tgcli/cli.py"
    cli.write_text(cli.read_text() + "#\n" * 50)

    result = _run(tmp_path)

    assert result.returncode == 0, result.stdout


def test_architecture_check_rejects_growth_past_the_grace_band(tmp_path):
    _write_minimal_tree(tmp_path)
    cli = tmp_path / "src/tgcli/cli.py"
    cli.write_text(cli.read_text() + "#\n" * 51)

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "src/tgcli/cli.py has 800 lines; reviewed ceiling is 749" in result.stdout


def test_strict_mode_rejects_any_growth_past_the_ceiling(tmp_path):
    """--strict is the integrator's merge-time true-up: zero grace."""
    _write_minimal_tree(tmp_path)
    cli = tmp_path / "src/tgcli/cli.py"
    cli.write_text(cli.read_text() + "#\n")

    result = _run(tmp_path, "--strict")

    assert result.returncode == 1
    assert "src/tgcli/cli.py has 750 lines; reviewed ceiling is 749" in result.stdout


def test_repository_passes_architecture_check():
    result = _run(Path(__file__).parents[1])

    assert result.returncode == 0, result.stdout


def test_architecture_check_rejects_write_text_in_state_module(tmp_path):
    _write_minimal_tree(tmp_path)
    module = tmp_path / "src/tgcli/login_state.py"
    module.write_text("def save(path, text):\n    path.write_text(text)\n")

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "login_state.py:2 calls write_text" in result.stdout
    assert "tgcli.atomic.replace_text" in result.stdout


def test_deleted_python_module_needs_no_write_policy_registry_update(tmp_path):
    _write_minimal_tree(tmp_path)
    module = tmp_path / "src/tgcli/login_state.py"
    module.write_text("from tgcli import atomic\n")
    module.unlink()

    result = _run(tmp_path)

    assert result.returncode == 0, result.stdout


def test_architecture_check_accepts_atomic_writer_in_state_module(tmp_path):
    _write_minimal_tree(tmp_path)
    module = tmp_path / "src/tgcli/login_state.py"
    module.write_text(
        "from tgcli import atomic\n\n"
        "def save(path, text):\n    atomic.replace_text(path, text)\n"
    )

    result = _run(tmp_path)

    assert result.returncode == 0, result.stdout


def test_new_python_module_is_write_text_denied_by_default(tmp_path):
    _write_minimal_tree(tmp_path)
    module = tmp_path / "src/tgcli/new_state.py"
    module.write_text("def save(path, text):\n    path.write_text(text)\n")

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "src/tgcli/new_state.py:2 calls write_text" in result.stdout


def test_store_module_has_a_reviewed_ceiling():
    checker = runpy.run_path(str(SCRIPT))

    assert "src/tgcli/commands/store.py" in checker["CEILINGS"]


def test_local_and_ci_gates_enable_strict_architecture_checks():
    root = Path(__file__).parents[1]
    local_gate = (root / "scripts/gate.sh").read_text()
    ci = (root / ".github/workflows/ci.yml").read_text()

    assert "scripts/check-architecture.py --strict" in local_gate
    assert "scripts/check-architecture.py --strict" in ci
