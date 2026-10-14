"""scripts/mutate.py: which mutants it builds and what its exit code claims."""

from __future__ import annotations

import ast
import importlib.util
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).parents[1] / "scripts" / "mutate.py"


@pytest.fixture
def mutate(monkeypatch) -> ModuleType:
    name = "tgcli_mutate_script"
    spec = importlib.util.spec_from_file_location(name, SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)  # dataclasses look it up
    spec.loader.exec_module(module)
    return module


def _git(root: Path, *argv: str) -> None:
    subprocess.run(["git", *argv], cwd=root, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "old.py").write_text("x = 1\n")
    _git(tmp_path, "add", ".")
    _git(tmp_path, "commit", "-q", "-m", "base")
    return tmp_path


def test_mutant_slices_a_non_ascii_line_by_bytes(mutate):
    source = 'label = "…" + ("a" if size == 4 else "b")\n'

    [mutant] = mutate.mutants("m.py", source, {1})

    assert mutant.before == "size == 4"
    assert mutant.after == "(size != 4)"
    assert 'label = "…" + ("a" if (size != 4) else "b")' in mutant.source
    ast.parse(mutant.source)


def test_chained_comparison_yields_one_mutant_per_operator(mutate):
    found = mutate.mutants("m.py", "ok = 0 < x <= limit\n", {1})

    assert sorted(mutant.after for mutant in found) == [
        "(0 < x > limit)",
        "(0 >= x <= limit)",
    ]


def test_untracked_python_file_counts_as_fully_added(mutate, repo, monkeypatch):
    (repo / "src" / "new.py").write_text("a = 1\nb = a == 1\n")
    (repo / "src" / "notes.txt").write_text("not python\n")
    monkeypatch.setattr(mutate, "ROOT", repo)

    assert mutate.changed_lines("HEAD", ["src"]) == {"src/new.py": {1, 2}}


def test_limit_that_skips_mutants_does_not_exit_zero(mutate, repo, monkeypatch, capsys):
    (repo / "src" / "old.py").write_text("x = 1\ny = x == 1 or x != 2\n")
    monkeypatch.setattr(mutate, "ROOT", repo)
    # The unmutated suite passes; every mutant is killed.
    calls = []

    def fake_suite(tree, timeout):
        calls.append(tree)
        return 0 if len(calls) == 1 else 1

    monkeypatch.setattr(mutate, "run_suite", fake_suite)
    monkeypatch.setattr(sys, "argv", ["mutate.py", "--base", "HEAD", "--limit", "1"])

    code = mutate.main()

    assert code == 3
    assert "not run (--limit)" in capsys.readouterr().out
