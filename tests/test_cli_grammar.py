"""Grammar and pre-session checks owned by parser.py and preflight.py.

`--confirm` and `--write` are only gates if they have to be typed in full, and
a `--params` typo is a purely local mistake that must never cost a session
open. Both are decided before anything reaches the network.
"""

import pytest

from tgcli.cli import main

SAMPLE = """
default_account = "main"

[accounts.main]
api_id = 12345
api_hash = "abcdef0123456789"
"""


@pytest.fixture
def config_env(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE)
    monkeypatch.setenv("TGCLI_CONFIG", str(path))


def test_confirm_must_be_spelled_in_full(config_env, capsys):
    assert main(["accounts", "remove", "main", "--c", "--json"]) == 1

    captured = capsys.readouterr()
    assert captured.out == ""
    assert "unrecognized arguments: --c" in captured.err
