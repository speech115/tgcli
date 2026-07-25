import json

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
    assert captured.out == captured.err
    assert json.loads(captured.out)["error"]["code"] == "CONFIG"
    assert json.loads(captured.err)["error"]["code"] == "CONFIG"


def test_accounts_list_verbose_keeps_data_on_stdout_and_writes_debug_to_stderr(
    config_env, capsys
):
    code = main(["accounts", "list", "--readonly", "-v"])

    assert code == 0
    captured = capsys.readouterr()
    assert (
        "DEBUG tgcli.cli: completed command=accounts exit_code=0 duration_ms="
        in captured.err
    )
    assert captured.out == "main | main\n"


def test_accounts_list_plain_output_is_tsv(config_env, capsys):
    code = main(["accounts", "list", "--plain"])

    assert code == 0
    assert capsys.readouterr().out == "main\tmain\n"


def test_malformed_command_returns_runtime_error_not_system_exit(capsys):
    code = main(["accounts", "missing"])

    captured = capsys.readouterr()
    assert code == 1
    assert captured.out == ""
    assert "invalid choice" in captured.err
