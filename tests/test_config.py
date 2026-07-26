import pytest

from tgcli.config import load_config, resolve_account
from tgcli.errors import ConfigError

SAMPLE = """
default_account = "main"

[accounts.main]
api_id = 12345
api_hash = "abcdef0123456789"

[accounts.pl]
api_id = 67890
api_hash = "fedcba9876543210"
session = "poland"
"""


@pytest.fixture
def config_file(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE)
    return path


def test_load_config_parses_accounts(config_file):
    config = load_config(config_file)
    assert config.default_account == "main"
    assert config.accounts["main"].api_id == 12345
    assert config.accounts["main"].session == "main"  # defaults to alias
    assert config.accounts["pl"].session == "poland"


def test_missing_config_raises_config_error(tmp_path):
    with pytest.raises(ConfigError):
        load_config(tmp_path / "nope.toml")


def test_account_missing_api_id_raises(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('[accounts.bad]\napi_hash = "x"\n')
    with pytest.raises(ConfigError):
        load_config(path)


def test_non_table_accounts_raises(tmp_path):
    path = tmp_path / "config.toml"
    path.write_text('accounts = "main"\n')
    with pytest.raises(ConfigError):
        load_config(path)


@pytest.mark.parametrize("entry", ['"x"', "[1, 2]", "5"])
def test_non_table_account_entry_raises(tmp_path, entry):
    path = tmp_path / "config.toml"
    path.write_text(f"[accounts]\nmain = {entry}\n")
    with pytest.raises(ConfigError):
        load_config(path)


@pytest.mark.parametrize("api_id", ['"abc"', "[1]"])
def test_non_numeric_api_id_raises(tmp_path, api_id):
    path = tmp_path / "config.toml"
    path.write_text(f'[accounts.main]\napi_id = {api_id}\napi_hash = "x"\n')
    with pytest.raises(ConfigError):
        load_config(path)


def test_non_table_account_entry_exits_3_through_cli(tmp_path, monkeypatch, capsys):
    import json

    from tgcli.cli import main

    path = tmp_path / "config.toml"
    path.write_text('[accounts]\nmain = "x"\n')
    monkeypatch.setenv("TGCLI_CONFIG", str(path))
    assert main(["--json", "accounts", "list"]) == 3
    assert json.loads(capsys.readouterr().out)["error"]["code"] == "CONFIG"


def test_resolve_priority_flag_env_default(config_file, monkeypatch):
    config = load_config(config_file)
    monkeypatch.setenv("TGCLI_ACCOUNT", "pl")
    assert resolve_account(config, "main").alias == "main"  # flag wins
    assert resolve_account(config, None).alias == "pl"  # env wins
    monkeypatch.delenv("TGCLI_ACCOUNT")
    assert resolve_account(config, None).alias == "main"  # config default


def test_resolve_unknown_alias_raises(config_file):
    config = load_config(config_file)
    with pytest.raises(ConfigError):
        resolve_account(config, "ghost")
