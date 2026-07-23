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


def test_mutual_chats_returns_common_chats(config_env, monkeypatch, capsys):
    user = ns(
        id=111,
        first_name="Alice",
        last_name="Smith",
        username="alice",
        bot=False,
        contact=True,
    )
    chat = ns(id=200, title="Shared Group", username="shared", megagroup=True)
    client = FakeClient(
        entities={"@alice": user},
        common_chats_result=ns(chats=[chat]),
    )
    make_session_fake(monkeypatch, client)

    assert main(["mutual-chats", "@alice", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["peer"]["id"] == 111
    assert data["count"] == 1
    assert data["chats"] == [
        {
            "id": 200,
            "type": "group",
            "username": "shared",
            "display_name": "Shared Group",
            "is_contact": False,
            "is_bot": False,
        }
    ]
    assert len(client.call_requests) == 1
    assert isinstance(client.call_requests[0], functions.messages.GetCommonChatsRequest)
    assert client.call_requests[0].user_id == "@alice"
    assert client.call_requests[0].max_id == 0
    assert client.call_requests[0].limit == 100


def test_mutual_chats_empty_list_is_success(config_env, monkeypatch, capsys):
    user = ns(
        id=111,
        first_name="Alice",
        last_name=None,
        username="alice",
        bot=False,
        contact=False,
    )
    client = FakeClient(
        entities={"@alice": user},
        common_chats_result=ns(chats=[]),
    )
    make_session_fake(monkeypatch, client)

    assert main(["mutual-chats", "@alice", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["chats"] == []
    assert data["count"] == 0


def test_mutual_chats_missing_user_exits_4(config_env, monkeypatch):
    client = FakeClient(entities={})
    make_session_fake(monkeypatch, client)
    assert main(["mutual-chats", "@missing", "--json"]) == 4


def test_mutual_chats_rejects_non_user(config_env, monkeypatch):
    channel = ns(id=5, title="Chan", username="chan", broadcast=True)
    client = FakeClient(entities={"@chan": channel})
    make_session_fake(monkeypatch, client)
    assert main(["mutual-chats", "@chan", "--json"]) == 2
