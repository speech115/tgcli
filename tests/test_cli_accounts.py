import json

import pytest

from tgcli.cli import main

SAMPLE = '''
default_account = "main"

[accounts.main]
api_id = 12345
api_hash = "abcdef0123456789"
'''


@pytest.fixture
def config_env(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE)
    monkeypatch.setenv("TGCLI_CONFIG", str(path))
    return path


def test_accounts_list_json(config_env, capsys):
    code = main(["--json", "accounts", "list"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data == {
        "default_account": "main",
        "accounts": [{"alias": "main", "session": "main"}],
    }


def test_accounts_list_never_leaks_api_hash(config_env, capsys):
    main(["--json", "accounts", "list"])
    assert "abcdef" not in capsys.readouterr().out


def test_missing_config_exits_3(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("TGCLI_CONFIG", str(tmp_path / "nope.toml"))
    code = main(["--json", "accounts", "list"])
    assert code == 3
    captured = capsys.readouterr()
    assert captured.out == ""
    assert json.loads(captured.err)["error"]["code"] == "CONFIG"
