"""scripts/repro.py: which tests it picks and what its exit code claims."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "repro.py"


@pytest.fixture
def repro(monkeypatch) -> ModuleType:
    name = "tgcli_repro_script"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


def _git(root: Path, *argv: str) -> None:
    subprocess.run(["git", *argv], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path, repro, monkeypatch) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "src").mkdir()
    # The bug the branch fixes: BASE's double returns its argument.
    (tmp_path / "src" / "calc.py").write_text("def double(x):\n    return x\n")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_old.py").write_text("def test_old():\n    pass\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "base")
    monkeypatch.setattr(repro, "ROOT", tmp_path)
    monkeypatch.setattr(sys, "argv", ["repro.py", "--base", "HEAD"])
    return tmp_path


def test_a_test_that_fails_on_base_reproduces(repro, repo, capsys):
    (repo / "src" / "calc.py").write_text("def double(x):\n    return 2 * x\n")
    (repo / "tests" / "test_calc.py").write_text(
        "from calc import double\n\ndef test_int():\n    assert double(2) == 4\n"
    )

    assert repro.main() == 0

    out = capsys.readouterr().out
    assert "FAILS ON BASE\ttests.test_calc::test_int" in out
    assert "test_old" not in out


def test_a_test_that_passes_on_base_does_not_reproduce(repro, repo, capsys):
    (repo / "tests" / "test_calc.py").write_text(
        "from calc import double\n\ndef test_zero():\n    assert double(0) == 0\n"
    )

    assert repro.main() == 1
    assert "0 of 1 changed tests fail on HEAD" in capsys.readouterr().out


def test_no_changed_test_exits_two(repro, repo):
    (repo / "src" / "calc.py").write_text("def double(x):\n    return 2 * x\n")

    assert repro.main() == 2
