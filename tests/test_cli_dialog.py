import json

import pytest
from telethon.tl import functions

from tests.conftest import FakeClient, make_session_fake, ns
from tgcli import safety
from tgcli.cli import main
from tgcli import session


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


def make_client():
    return FakeClient(entities={"@chan": ns(id=5, title="Chan")})


def test_dialog_pin_records_toggle_and_audit(config_env, monkeypatch, capsys):
    client = make_client()
    make_session_fake(monkeypatch, client)

    assert main(["dialog", "pin", "@chan", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "dialog": {"id": 5},
        "pinned": True,
    }
    assert len(client.call_requests) == 1
    request = client.call_requests[0]
    assert isinstance(request, functions.messages.ToggleDialogPinRequest)
    assert request.pinned is True
    actions = [
        json.loads(line)["action"]
        for line in safety.audit_path().read_text().splitlines()
    ]
    assert actions == ["dialog-pin"]
    assert main(["--readonly", "dialog", "pin", "@chan"]) == 2


def test_dialog_unpin_records_toggle_and_audit(config_env, monkeypatch, capsys):
    client = make_client()
    make_session_fake(monkeypatch, client)

    assert main(["dialog", "unpin", "@chan", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "dialog": {"id": 5},
        "pinned": False,
    }
    request = client.call_requests[0]
    assert isinstance(request, functions.messages.ToggleDialogPinRequest)
    assert request.pinned is False
    actions = [
        json.loads(line)["action"]
        for line in safety.audit_path().read_text().splitlines()
    ]
    assert actions == ["dialog-unpin"]
    assert main(["--readonly", "dialog", "unpin", "@chan"]) == 2


@pytest.mark.parametrize(
    "flag,argv",
    [
        ("--readonly", ["--readonly", "dialog", "pin", "@chan"]),
        ("TGCLI_READONLY", ["dialog", "pin", "@chan"]),
        ("TGCLI_NO_SEND", ["dialog", "unpin", "@chan"]),
    ],
)
def test_dialog_pin_gates_before_config_or_session(monkeypatch, flag, argv):
    from tgcli import cli

    monkeypatch.setattr(cli, "load_config", lambda: pytest.fail("config loaded"))
    monkeypatch.setattr(
        session, "client", lambda account: pytest.fail("session opened")
    )
    if flag.startswith("TGCLI_"):
        monkeypatch.setenv(flag, "1")

    assert main(argv) == 2
