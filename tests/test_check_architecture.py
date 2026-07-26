from __future__ import annotations

import runpy
import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "check-architecture.py"
CEILINGS = {
    "src/tgcli/cli.py": 533,
    "src/tgcli/parser.py": 516,
    "src/tgcli/preflight.py": 254,
    "src/tgcli/dispatch.py": 257,
    "src/tgcli/commands/batch.py": 96,
    "src/tgcli/read_ops.py": 434,
    "src/tgcli/commands/clone.py": 1199,
    "src/tgcli/clone/state.py": 332,
    "src/tgcli/clone/quotes.py": 391,
    "src/tgcli/clone/quote_fallback.py": 127,
}
STATE_WRITER_MODULES = (
    "src/tgcli/safety.py",
    "src/tgcli/login_state.py",
    "src/tgcli/resolve_phone.py",
    "src/tgcli/config.py",
    "src/tgcli/commands/accounts.py",
    "src/tgcli/commands/media.py",
    "src/tgcli/commands/store.py",
    "src/tgcli/clone/state.py",
    "src/tgcli/clone/flood.py",
)


def _run(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), "--root", str(root)],
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


def test_architecture_check_rejects_growth_past_the_ceiling(tmp_path):
    _write_minimal_tree(tmp_path)
    cli = tmp_path / "src/tgcli/cli.py"
    cli.write_text(cli.read_text() + "#\n")

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "src/tgcli/cli.py has 534 lines; reviewed ceiling is 533" in result.stdout


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
