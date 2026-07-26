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


def test_info_json_projects_channel_without_access_hash(
    config_env, monkeypatch, capsys
):
    entity = ns(
        id=-1001234,
        title="Channel",
        username="chan",
        broadcast=True,
        access_hash=987654321,
    )
    make_session_fake(monkeypatch, FakeClient(entities={"@chan": entity}))

    code = main(["info", "@chan", "--json"])

    assert code == 0
    assert json.loads(capsys.readouterr().out) == {
        "id": -1001234,
        "name": "Channel",
        "kind": "channel",
        "username": "chan",
    }


def test_info_accepts_numeric_dialog_id(config_env, monkeypatch, capsys):
    entity = ns(id=-1001234, title="Channel", username=None, broadcast=True)
    make_session_fake(monkeypatch, FakeClient(entities={-1001234: entity}))

    code = main(["info", "-1001234", "--json"])

    assert code == 0
    assert json.loads(capsys.readouterr().out)["id"] == -1001234


def test_info_json_projects_user_without_access_hash(config_env, monkeypatch, capsys):
    entity = ns(
        id=1234,
        first_name="Alice",
        last_name="Smith",
        username="alice",
        access_hash=987654321,
    )
    make_session_fake(monkeypatch, FakeClient(entities={"@alice": entity}))

    code = main(["info", "@alice", "--json"])

    assert code == 0
    assert json.loads(capsys.readouterr().out) == {
        "id": 1234,
        "name": "Alice Smith",
        "kind": "user",
        "username": "alice",
    }


def test_info_full_reports_role_rights_and_slowmode(config_env, monkeypatch, capsys):
    entity = ns(
        id=5,
        title="Chan",
        username="chan",
        broadcast=False,
        megagroup=True,
        creator=False,
        admin_rights=ns(
            delete_messages=True,
            pin_messages=True,
            edit_messages=False,
        ),
        banned_rights=None,
        default_banned_rights=None,
    )

    class FullClient(FakeClient):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.full_requests = []

        async def __call__(self, request):
            self.full_requests.append(request)
            return ns(
                full_chat=ns(
                    slowmode_seconds=30,
                    participants_count=12,
                    about="rules",
                )
            )

    client = FullClient(entities={"@chan": entity})
    make_session_fake(monkeypatch, client)

    assert main(["info", "@chan", "--full", "--json"]) == 0

    data = json.loads(capsys.readouterr().out)
    assert data["role"] == "admin"
    assert data["can"]["send_messages"] is True
    assert data["can"]["delete_messages"] is True
    assert data["slowmode_seconds"] == 30
    assert data["participants_count"] == 12
    assert data["about"] == "rules"
    assert len(client.full_requests) == 1
    assert isinstance(client.full_requests[0], functions.channels.GetFullChannelRequest)


def test_info_full_creator_has_all_capabilities(config_env, monkeypatch, capsys):
    entity = ns(
        id=8,
        title="Owned channel",
        username="owned",
        broadcast=True,
        megagroup=False,
        creator=True,
        admin_rights=None,
    )

    class FullClient(FakeClient):
        async def __call__(self, request):
            return ns(full_chat=ns())

    client = FullClient(entities={"@owned": entity})
    make_session_fake(monkeypatch, client)

    assert main(["info", "@owned", "--full", "--json"]) == 0

    data = json.loads(capsys.readouterr().out)
    assert data["can"] == {
        "send_messages": True,
        "send_media": True,
        "pin_messages": True,
        "delete_messages": True,
        "edit_messages": True,
    }


def test_info_full_admin_without_pin_right_cannot_pin(config_env, monkeypatch, capsys):
    entity = ns(
        id=9,
        title="Restricted admin",
        username="restricted",
        broadcast=False,
        megagroup=True,
        creator=False,
        admin_rights=ns(
            delete_messages=True,
            pin_messages=False,
            edit_messages=True,
        ),
    )

    class FullClient(FakeClient):
        async def __call__(self, request):
            return ns(full_chat=ns())

    client = FullClient(entities={"@restricted": entity})
    make_session_fake(monkeypatch, client)

    assert main(["info", "@restricted", "--full", "--json"]) == 0

    data = json.loads(capsys.readouterr().out)
    assert data["can"]["pin_messages"] is False


def test_info_full_broadcast_admin_without_post_right_cannot_send(
    config_env, monkeypatch, capsys
):
    entity = ns(
        id=10,
        title="No posts",
        username="no_posts",
        broadcast=True,
        megagroup=False,
        creator=False,
        admin_rights=ns(
            post_messages=False,
            delete_messages=True,
            pin_messages=True,
            edit_messages=True,
        ),
    )

    class FullClient(FakeClient):
        async def __call__(self, request):
            return ns(full_chat=ns())

    client = FullClient(entities={"@no_posts": entity})
    make_session_fake(monkeypatch, client)

    assert main(["info", "@no_posts", "--full", "--json"]) == 0

    data = json.loads(capsys.readouterr().out)
    assert data["can"]["send_messages"] is False
    assert data["can"]["send_media"] is False


def test_info_full_for_user_avoids_channel_request(config_env, monkeypatch, capsys):
    entity = ns(id=6, first_name="Alice", username="alice")

    class FullClient(FakeClient):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.full_requests = []

        async def __call__(self, request):
            self.full_requests.append(request)
            raise AssertionError("user info must not request full channel metadata")

    client = FullClient(entities={"@alice": entity})
    make_session_fake(monkeypatch, client)

    assert main(["info", "@alice", "--full", "--json"]) == 0

    data = json.loads(capsys.readouterr().out)
    assert data["role"] is None
    assert data["can"] == {
        "send_messages": True,
        "send_media": True,
        "pin_messages": True,
        "delete_messages": True,
        "edit_messages": False,
    }
    assert data["slowmode_seconds"] is None
    assert data["participants_count"] is None
    assert data["about"] is None
    assert client.full_requests == []


def test_info_full_for_basic_group_has_no_channel_metadata(
    config_env, monkeypatch, capsys
):
    entity = ns(id=-7, title="Basic group", username=None)

    class FullClient(FakeClient):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)
            self.full_requests = []

        async def __call__(self, request):
            self.full_requests.append(request)
            raise AssertionError(
                "basic group info must not request full channel metadata"
            )

    client = FullClient(entities={-7: entity})
    make_session_fake(monkeypatch, client)

    assert main(["info", "-7", "--full", "--json"]) == 0

    data = json.loads(capsys.readouterr().out)
    assert data["role"] == "member"
    assert data["can"]["send_messages"] is None
    assert data["slowmode_seconds"] is None
    assert data["participants_count"] is None
    assert data["about"] is None
    assert client.full_requests == []
