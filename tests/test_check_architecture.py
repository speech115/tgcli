from __future__ import annotations

import subprocess
import sys
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / "scripts" / "check-architecture.py"
BASELINES = {
    "src/tgcli/cli.py": 909,
    "src/tgcli/commands/batch.py": 96,
    "src/tgcli/read_ops.py": 413,
    "src/tgcli/commands/clone.py": 874,
    "src/tgcli/clone/state.py": 287,
}


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
) -> None:
    prefixes = {
        "src/tgcli/cli.py": cli_import,
        "src/tgcli/commands/batch.py": batch_import,
        "src/tgcli/read_ops.py": "",
        "src/tgcli/commands/clone.py": "",
        "src/tgcli/clone/state.py": "",
    }
    for relative, prefix in prefixes.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        prefix_lines = prefix.splitlines()
        padding = ["#"] * (BASELINES[relative] - len(prefix_lines))
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


def test_architecture_check_requires_lowering_a_shrunk_baseline(tmp_path):
    _write_minimal_tree(tmp_path)
    cli = tmp_path / "src/tgcli/cli.py"
    cli.write_text("\n".join(cli.read_text().splitlines()[:-1]) + "\n")

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "src/tgcli/cli.py has 908 lines; reviewed baseline is 909" in result.stdout


def test_repository_passes_architecture_check():
    result = _run(Path(__file__).parents[1])

    assert result.returncode == 0, result.stdout
