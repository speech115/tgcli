from __future__ import annotations

import importlib.util
import json
from pathlib import Path
from types import ModuleType

import pytest

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "check-comments.py"


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location("check_comments", SCRIPT)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def repo(tmp_path, monkeypatch):
    module = _load()
    (tmp_path / "scripts").mkdir()
    monkeypatch.setattr(module, "ROOT", tmp_path)
    monkeypatch.setattr(module, "BASELINE", tmp_path / "scripts" / "baseline.json")

    def tracked():
        return sorted(
            str(path.relative_to(tmp_path))
            for path in tmp_path.rglob("*")
            if path.is_file() and path.name != "baseline.json"
        )

    monkeypatch.setattr(module, "_tracked", tracked)

    def write(files: dict[str, str], baseline: dict[str, int]) -> None:
        for name, text in files.items():
            (tmp_path / name).write_text(text)
        module.BASELINE.write_text(json.dumps(baseline))

    return module, write


def test_a_new_comment_fails(repo, capsys):
    module, write = repo
    write({"a.py": "x = 1  # one\n"}, {})
    assert module.main([]) == 1
    assert "a.py:1: # one" in capsys.readouterr().err


def test_directives_and_shebang_are_not_comments(repo):
    module, write = repo
    write(
        {
            "a.py": "#!/usr/bin/env python3\n"
            "import os  # noqa: F401\n"
            "x: int = None  # type: ignore[assignment]\n"
            "if False:  # pragma: no cover\n"
            "    pass\n"
        },
        {},
    )
    assert module.main([]) == 0


def test_a_directive_with_a_reason_counts(repo):
    module, write = repo
    write({"a.py": "x: int = None  # type: ignore  # stub gap\n"}, {})
    assert module.main([]) == 1


def test_shell_and_yaml_comments_count(repo):
    module, write = repo
    write({"a.sh": "#!/bin/sh\n# why\necho $#\n", "b.yml": "k: v # pin\n"}, {})
    assert module.main([]) == 1
    write({}, {"a.sh": 1, "b.yml": 1})
    assert module.main([]) == 0


def test_baseline_only_ratchets_down(repo):
    module, write = repo
    write({"a.py": "x = 1\n# kept\n"}, {"a.py": 2})
    assert module.main([]) == 1
    assert module.main(["--update"]) == 0
    assert json.loads(module.BASELINE.read_text()) == {"a.py": 1}
    write({"a.py": "# one\n# two\n"}, {"a.py": 1})
    assert module.main(["--update"]) == 1
    assert json.loads(module.BASELINE.read_text()) == {"a.py": 1}
