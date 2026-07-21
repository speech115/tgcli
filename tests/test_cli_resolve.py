import json

import pytest
from telethon.tl import functions

from tests.conftest import FakeClient, make_session_fake, ns
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


def test_resolve_username_returns_user_peer(config_env, monkeypatch, capsys):
    entity = ns(
        id=111,
        first_name="Alice",
        last_name="Smith",
        username="alice",
        bot=False,
        contact=True,
    )
    client = FakeClient(entities={"@alice": entity})
    make_session_fake(monkeypatch, client)

    assert main(["resolve", "@alice", "--json"]) == 0

    data = json.loads(capsys.readouterr().out)
    assert data == {
        "peer": {
            "id": 111,
            "type": "user",
            "username": "alice",
            "display_name": "Alice Smith",
            "is_contact": True,
            "is_bot": False,
        }
    }


def test_resolve_bot_reports_bot_type(config_env, monkeypatch, capsys):
    entity = ns(
        id=222,
        first_name="Helper",
        last_name=None,
        username="helper_bot",
        bot=True,
        contact=False,
    )
    client = FakeClient(entities={"@helper_bot": entity})
    make_session_fake(monkeypatch, client)

    assert main(["resolve", "@helper_bot", "--json"]) == 0

    data = json.loads(capsys.readouterr().out)
    assert data["peer"]["type"] == "bot"
    assert data["peer"]["is_bot"] is True


def test_resolve_phone_uses_resolve_phone_request_only(config_env, monkeypatch, capsys):
    from telethon.tl import types

    user = ns(
        id=333,
        first_name="Phoney",
        last_name=None,
        username=None,
        bot=False,
        contact=False,
    )
    resolved = ns(peer=types.PeerUser(user_id=333), users=[user], chats=[])
    client = FakeClient(resolve_phone_result=resolved)
    make_session_fake(monkeypatch, client)

    assert main(["resolve", "+99512345678", "--json"]) == 0

    data = json.loads(capsys.readouterr().out)
    assert data["peer"]["id"] == 333
    assert data["peer"]["type"] == "user"

    resolve_requests = [
        request
        for request in client.call_requests
        if isinstance(request, functions.contacts.ResolvePhoneRequest)
    ]
    assert len(resolve_requests) == 1
    assert resolve_requests[0].phone == "99512345678"

    assert not any(
        isinstance(request, functions.contacts.ImportContactsRequest)
        for request in client.call_requests
    )


def test_resolve_phone_empty_result_is_not_found(config_env, monkeypatch, capsys):
    from telethon.tl import types

    resolved = ns(peer=types.PeerUser(user_id=999), users=[], chats=[])
    client = FakeClient(resolve_phone_result=resolved)
    make_session_fake(monkeypatch, client)

    assert main(["resolve", "+99500000000", "--json"]) == 4
    error = json.loads(capsys.readouterr().err)
    assert error["error"]["code"] == "NOT_FOUND"

    assert not any(
        isinstance(request, functions.contacts.ImportContactsRequest)
        for request in client.call_requests
    )


def test_resolve_plain_output_is_single_row(config_env, monkeypatch, capsys):
    entity = ns(
        id=444,
        title="Broadcast",
        username="broadcast",
        broadcast=True,
        bot=False,
        contact=False,
    )
    client = FakeClient(entities={"@broadcast": entity})
    make_session_fake(monkeypatch, client)

    assert main(["resolve", "@broadcast", "--plain"]) == 0

    out = capsys.readouterr().out.strip("\n")
    assert out == "444\tchannel\tbroadcast\tBroadcast"
