from __future__ import annotations

import runpy
import subprocess
import sys
from pathlib import Path

SCRIPT = Path(__file__).parents[1] / "scripts" / "check-architecture.py"
MAX_LINES = runpy.run_path(str(SCRIPT))["MAX_LINES"]


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
    dispatch_import: str = "",
) -> None:
    modules = {
        "src/tgcli/cli.py": cli_import,
        "src/tgcli/dispatch.py": dispatch_import,
    }
    for relative, source in modules.items():
        path = root / relative
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(source)


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
    )

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "src/tgcli/cli.py imports read command module dialogs" in result.stdout


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


def test_architecture_check_accepts_owned_read_operation_seam(tmp_path):
    _write_minimal_tree(tmp_path)

    result = _run(tmp_path)

    assert result.returncode == 0
    assert result.stdout == "architecture check passed\n"


def test_architecture_check_rejects_read_dispatch_leaking_into_dispatch(tmp_path):
    _write_minimal_tree(
        tmp_path,
        dispatch_import="from tgcli.commands import search as search_cmd\n",
    )

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "src/tgcli/dispatch.py imports read command module search" in result.stdout


def test_architecture_check_rejects_a_module_past_the_line_limit(tmp_path):
    _write_minimal_tree(tmp_path)
    module = tmp_path / "src/tgcli/big.py"
    module.write_text("#\n" * MAX_LINES)
    assert _run(tmp_path).returncode == 0

    module.write_text("#\n" * (MAX_LINES + 1))
    result = _run(tmp_path)

    assert result.returncode == 1
    expected = f"big.py has {MAX_LINES + 1} lines; the limit is {MAX_LINES}"
    assert expected in result.stdout


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


def test_architecture_check_rejects_a_test_importing_another_test_module(tmp_path):
    _write_minimal_tree(tmp_path)
    tests = tmp_path / "tests"
    tests.mkdir()
    (tests / "test_a.py").write_text("from tests.test_b import helper\n")
    (tests / "test_c.py").write_text("def t():\n    from tests import test_b\n")
    (tests / "test_d.py").write_text("from .test_b import helper\n")
    (tests / "test_e.py").write_text("from tests.live.test_smoke import helper\n")
    (tests / "test_f.py").write_text("import tests.live.test_smoke\n")

    result = _run(tmp_path)

    assert result.returncode == 1
    for name in (
        "test_a.py:1",
        "test_c.py:2",
        "test_d.py:1",
        "test_e.py:1",
        "test_f.py:1",
    ):
        assert f"tests/{name} imports a test module" in result.stdout


def test_architecture_check_accepts_tests_importing_fakes_and_conftest(tmp_path):
    _write_minimal_tree(tmp_path)
    tests = tmp_path / "tests"
    (tests / "fakes").mkdir(parents=True)
    (tests / "test_a.py").write_text(
        "from tests.conftest import FakeClient\n"
        "from tests.fakes.media import media_message\n"
        "from tests.fakes import messages\n"
    )

    result = _run(tmp_path)

    assert result.returncode == 0, result.stdout


def test_architecture_check_rejects_a_bare_leg_annotation(tmp_path):
    _write_minimal_tree(tmp_path)
    module = tmp_path / "src/tgcli/clone/replies.py"
    module.parent.mkdir(parents=True)
    module.write_text(
        "from tgcli.clone import legs\n"
        "from tgcli.clone.legs import AnyLeg, Leg\n"
        "def a(leg: Leg) -> None: ...\n"
        "def b(*, leg: legs.Leg | None = None) -> None: ...\n"
        "def c(leg: AnyLeg) -> legs.PostsLeg: ...\n"
    )

    result = _run(tmp_path)

    assert result.returncode == 1
    assert "src/tgcli/clone/replies.py:3 annotates a bare Leg" in result.stdout
    assert "src/tgcli/clone/replies.py:4 annotates a bare Leg" in result.stdout
    assert "replies.py:5" not in result.stdout
