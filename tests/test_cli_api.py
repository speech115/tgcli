import json

import pytest
from telethon import errors as telethon_errors
from telethon.tl import types

from tests.conftest import make_session_fake
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


class ApiClient:
    async def get_input_entity(self, value):
        assert value == "@self"
        return types.InputPeerChannel(channel_id=1, access_hash=2)

    async def __call__(self, request):
        assert request.__class__.__name__ == "GetFullUserRequest"
        assert isinstance(request.id, types.InputUserSelf)

        class Result:
            def to_dict(self):
                return {"_": "UserFull", "id": 42}

        return Result()


def test_api_json_emits_raw_tl_envelope(config_env, monkeypatch, capsys):
    make_session_fake(monkeypatch, ApiClient())

    assert (
        main(["api", "users.getFullUser", "--params", '{"id":"@self"}', "--json"]) == 0
    )

    assert json.loads(capsys.readouterr().out) == {
        "method": "users.getFullUser",
        "result": {"_": "UserFull", "id": 42},
    }


def test_api_floodwait_maps_to_exit_5(config_env, monkeypatch, capsys):
    class FloodClient(ApiClient):
        async def __call__(self, request):
            raise telethon_errors.FloodWaitError(request=None, capture=42)

    make_session_fake(monkeypatch, FloodClient())

    assert (
        main(["api", "users.getFullUser", "--params", '{"id":"@self"}', "--json"]) == 5
    )

    assert json.loads(capsys.readouterr().err)["error"] == {
        "code": "FLOOD_WAIT",
        "message": "rate limited for 42s",
        "retry_after": 42,
    }


class NumericPeerClient:
    """Rejects raw strings the way Telethon does — a digit string is a phone."""

    def __init__(self):
        self.lookups = []

    async def get_input_entity(self, value):
        if isinstance(value, str):
            raise ValueError(f'Cannot find any entity corresponding to "{value}"')
        self.lookups.append(value)
        return types.InputPeerChannel(channel_id=3890108644, access_hash=7)

    async def __call__(self, request):
        assert request.__class__.__name__ == "GetFullChannelRequest"
        assert isinstance(request.channel, types.InputChannel)

        class Result:
            def to_dict(self):
                return {"_": "messages.ChatFull"}

        return Result()


def test_api_resolves_numeric_peer_alias_through_chatref(
    config_env, monkeypatch, capsys
):
    client = NumericPeerClient()
    make_session_fake(monkeypatch, client)

    assert (
        main(
            [
                "api",
                "channels.getFullChannel",
                "--params",
                '{"channel":"-1003890108644"}',
                "--json",
            ]
        )
        == 0
    )

    assert client.lookups == [-1003890108644]
    assert json.loads(capsys.readouterr().out)["result"] == {"_": "messages.ChatFull"}


def test_api_unresolvable_numeric_peer_alias_exits_not_found(
    config_env, monkeypatch, capsys
):
    class Client(NumericPeerClient):
        async def get_input_entity(self, value):
            raise ValueError(f'Cannot find any entity corresponding to "{value}"')

    make_session_fake(monkeypatch, Client())

    assert (
        main(
            [
                "api",
                "channels.getFullChannel",
                "--params",
                '{"channel":"-1003890108644"}',
                "--json",
            ]
        )
        == 4
    )

    assert json.loads(capsys.readouterr().err)["error"]["code"] == "NOT_FOUND"


def test_api_write_kill_switch_blocks_without_opening_a_session(monkeypatch):

    monkeypatch.setattr(
        session, "client", lambda account: pytest.fail("session opened")
    )
    monkeypatch.setenv("TGCLI_NO_SEND", "1")

    assert main(["api", "messages.sendMessage", "--params", "{}", "--write"]) == 2
