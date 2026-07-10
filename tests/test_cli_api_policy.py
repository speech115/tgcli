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


def test_read_api_method_reaches_network_dispatcher(config_env, monkeypatch):
    from tgcli import cli

    called = []

    async def fake_run_network(args, account):
        called.append((args.method, args.params, account.alias))
        return {"method": args.method, "result": {}}, []

    monkeypatch.setattr(cli, "_run_network", fake_run_network)

    assert main(["api", "users.getFullUser", "--params", '{"id": "@self"}', "--json"]) == 0
    assert called == [("users.getFullUser", '{"id": "@self"}', "main")]


def test_write_api_method_is_blocked_before_network(config_env, monkeypatch, capsys):
    from tgcli import cli

    monkeypatch.setattr(cli.session, "client", lambda account: pytest.fail("session opened"))

    assert main(["api", "messages.sendMessage", "--params", "{}", "--write"]) == 2
    assert "phase 4" in capsys.readouterr().err


def test_write_read_api_method_without_params_is_blocked_by_policy(config_env, capsys):
    assert main(["api", "users.getFullUser", "--write"]) == 2
    assert "phase 4" in capsys.readouterr().err


def test_read_api_method_without_params_remains_parser_error(config_env, capsys):
    assert main(["api", "users.getFullUser"]) == 1
    assert "the following arguments are required: --params" in capsys.readouterr().err


def test_non_read_api_method_is_blocked_before_network(config_env, monkeypatch):
    from tgcli import cli

    monkeypatch.setattr(cli.session, "client", lambda account: pytest.fail("session opened"))

    assert main(["api", "messages.sendMessage", "--params", "{}"]) == 2


@pytest.mark.parametrize("method", ["auth.checkPassword", "account.getTmpPassword"])
def test_sensitive_verb_prefixed_api_method_is_blocked_before_config_or_session(
    method, monkeypatch, capsys
):
    from tgcli import cli

    monkeypatch.setattr(cli, "load_config", lambda: pytest.fail("config loaded"))
    monkeypatch.setattr(cli.session, "client", lambda account: pytest.fail("session opened"))
    monkeypatch.setattr(cli, "_run_network", lambda args, account: pytest.fail("network dispatched"))

    assert main(["api", method, "--params", "{}"]) == 2
    assert "raw API method is not allowlisted for read-only use" in capsys.readouterr().err
