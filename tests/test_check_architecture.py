from __future__ import annotations

import runpy
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "check-architecture.py"
CEILINGS = {
    "src/tgcli/cli.py": 663,
    "src/tgcli/parser.py": 740,
    "src/tgcli/preflight.py": 440,
    "src/tgcli/dispatch.py": 327,
    "src/tgcli/commands/batch.py": 96,
    "src/tgcli/read_ops.py": 437,
    "src/tgcli/commands/clone.py": 1230,
    "src/tgcli/clone/state.py": 391,
    "src/tgcli/clone/quotes.py": 392,
    "src/tgcli/clone/quote_fallback.py": 127,
    "src/tgcli/archive/store.py": 1021,
    "src/tgcli/archive/sync.py": 596,
    "src/tgcli/archive/backfill.py": 320,
    "src/tgcli/archive/transcribe.py": 251,
    "src/tgcli/archive/explore.py": 578,
    "src/tgcli/archive/search.py": 78,
    "src/tgcli/archive/refresh.py": 196,
    "src/tgcli/archive/media.py": 72,
    "src/tgcli/commands/archive_refresh.py": 118,
    "src/tgcli/commands/archive.py": 546,
    "src/tgcli/governor/__init__.py": 14,
    "src/tgcli/governor/gate.py": 186,
    "src/tgcli/governor/ledger.py": 416,
    "src/tgcli/governor/pacing.py": 231,
    "src/tgcli/governor/probe.py": 86,
    "src/tgcli/governor/registry.py": 133,
    "src/tgcli/governor/seam.py": 66,
}
STATE_WRITER_MODULES = (
    "src/tgcli/safety.py",
    "src/tgcli/login_state.py",
    "src/tgcli/resolve_phone.py",
    "src/tgcli/config.py",
    "src/tgcli/commands/accounts.py",
    "src/tgcli/commands/media.py",
    "src/tgcli/commands/store.py",
)


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
    for relative in STATE_WRITER_MODULES:
        path = root / relative
        if path.exists():
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("from tgcli import atomic\n")


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
    assert STATE_WRITER_MODULES == checker["STATE_WRITER_MODULES"]
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
    assert "src/tgcli/cli.py has 664 lines; over ceiling 663" in result.stderr
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
    assert "src/tgcli/cli.py has 714 lines; reviewed ceiling is 663" in result.stdout


def test_strict_mode_rejects_any_growth_past_the_ceiling(tmp_path):
    """--strict is the integrator's merge-time true-up: zero grace."""
    _write_minimal_tree(tmp_path)
    cli = tmp_path / "src/tgcli/cli.py"
    cli.write_text(cli.read_text() + "#\n")

    result = _run(tmp_path, "--strict")

    assert result.returncode == 1
    assert "src/tgcli/cli.py has 664 lines; reviewed ceiling is 663" in result.stdout


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


def test_architecture_check_rejects_a_missing_state_module(tmp_path):
    """A renamed or deleted listed module must fail, never silently drop its
    write_text ban."""
    _write_minimal_tree(tmp_path)
    (tmp_path / "src/tgcli/login_state.py").unlink()

    result = _run(tmp_path)

    assert result.returncode == 1
    assert (
        "src/tgcli/login_state.py is missing; STATE_WRITER_MODULES must list "
        "real modules" in result.stdout
    )


def test_architecture_check_accepts_atomic_writer_in_state_module(tmp_path):
    _write_minimal_tree(tmp_path)
    module = tmp_path / "src/tgcli/login_state.py"
    module.write_text(
        "from tgcli import atomic\n\n"
        "def save(path, text):\n    atomic.replace_text(path, text)\n"
    )

    result = _run(tmp_path)

    assert result.returncode == 0, result.stdout
