import json
from datetime import datetime, timezone

import pytest
from telethon.tl import functions

from tests.conftest import FakeClient, make_session_fake, ns
from tgcli import safety
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


def make_client():
    return FakeClient(entities={"@chan": ns(id=5, title="Chan")})


def test_dialog_archive_and_unarchive(config_env, monkeypatch, capsys):
    client = make_client()
    make_session_fake(monkeypatch, client)

    assert main(["dialog", "archive", "@chan", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "dialog": {"id": 5},
        "archived": True,
    }
    request = client.call_requests[0]
    assert isinstance(request, functions.folders.EditPeerFoldersRequest)
    assert request.folder_peers[0].folder_id == 1

    assert main(["dialog", "unarchive", "@chan", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "dialog": {"id": 5},
        "archived": False,
    }
    assert client.call_requests[1].folder_peers[0].folder_id == 0
    actions = [
        json.loads(line)["action"]
        for line in safety.audit_path().read_text().splitlines()
    ]
    assert actions == ["dialog-archive", "dialog-unarchive"]


def test_dialog_mute_requires_until_or_forever(config_env, monkeypatch):
    client = make_client()
    make_session_fake(monkeypatch, client)
    assert main(["dialog", "mute", "@chan", "--json"]) == 2
    assert client.call_requests == []
    assert not safety.audit_path().exists()


def test_dialog_mute_until_and_forever(config_env, monkeypatch, capsys):
    client = make_client()
    make_session_fake(monkeypatch, client)
    until = "2030-01-01T00:00:00+00:00"

    assert main(["dialog", "mute", "@chan", "--until", until, "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data == {
        "dialog": {"id": 5},
        "muted": True,
        "until": until,
    }
    mute_req = client.call_requests[0]
    assert isinstance(mute_req, functions.account.UpdateNotifySettingsRequest)
    assert mute_req.settings.mute_until == int(
        datetime(2030, 1, 1, tzinfo=timezone.utc).timestamp()
    )

    assert main(["dialog", "mute", "@chan", "--forever", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["muted"] is True
    assert data["until"] is None
    assert client.call_requests[1].settings.mute_until == 2**31 - 1


def test_dialog_unmute(config_env, monkeypatch, capsys):
    client = make_client()
    make_session_fake(monkeypatch, client)
    assert main(["dialog", "unmute", "@chan", "--json"]) == 0
    assert json.loads(capsys.readouterr().out) == {
        "dialog": {"id": 5},
        "muted": False,
        "until": None,
    }
    request = client.call_requests[0]
    assert isinstance(request, functions.account.UpdateNotifySettingsRequest)
    assert request.settings.mute_until == 0
    actions = [
        json.loads(line)["action"]
        for line in safety.audit_path().read_text().splitlines()
    ]
    assert actions == ["dialog-unmute"]


def test_dialog_archive_readonly_gate(monkeypatch):
    from tgcli import cli

    monkeypatch.setattr(cli, "load_config", lambda: pytest.fail("config loaded"))
    assert main(["--readonly", "dialog", "archive", "@chan"]) == 2
