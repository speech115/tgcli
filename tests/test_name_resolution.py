"""A display name shared by a peer that refuses us still resolves the others.

The session file maps display names to peers. When a private channel and a
user carry the same name, a refusal on the channel must not abort the lookup.
"""

import json

import pytest
from telethon import errors as telethon_errors, utils
from telethon.sessions import SQLiteSession
from telethon.tl import functions, types

from tests.conftest import FakeClient, make_session_fake
from tests.fakes.messages import DATE, message
from tgcli.cli import main
from tgcli.client import Client

NAME = "Fixture Person"
USER_ID = 4242
CHANNEL_ID = 777


def _user() -> types.User:
    return types.User(USER_ID, access_hash=42, first_name="Fixture", last_name="Person")


def _private_channel() -> types.Channel:
    return types.Channel(
        CHANNEL_ID, NAME, types.ChatPhotoEmpty(), DATE, access_hash=77, broadcast=True
    )


class SessionFileClient(Client):
    """A real tgcli Client on a real session file; Telegram RPCs are stubbed."""

    def __init__(self, path, entities, *, accessible):
        super().__init__(SQLiteSession(str(path)), 1, "offline-placeholder")
        self.requests = []
        self.accessible = {utils.get_peer_id(entity) for entity in accessible}
        self._by_id = {utils.get_peer_id(entity): entity for entity in entities}
        users = [item for item in entities if isinstance(item, types.User)]
        chats = [item for item in entities if not isinstance(item, types.User)]
        self.session.process_entities(types.contacts.ResolvedPeer(None, chats, users))
        reachable = [self._by_id[peer_id] for peer_id in self.accessible]
        self.messages = FakeClient(
            messages=[message(7, item, message="needle") for item in reachable],
            message_total=1,
        )

    async def connect(self):
        raise AssertionError("No network connection allowed")

    def _fetch(self, input_peer):
        peer_id = utils.get_peer_id(input_peer)
        if peer_id not in self.accessible:
            raise telethon_errors.ChannelPrivateError(request=None)
        return self._by_id[peer_id]

    async def __call__(self, request, *args, **kwargs):
        self.requests.append(request)
        if isinstance(request, functions.users.GetUsersRequest):
            return [self._fetch(item) for item in request.id]
        if isinstance(request, functions.channels.GetChannelsRequest):
            return types.messages.Chats(chats=[self._fetch(i) for i in request.id])
        raise AssertionError(f"Unexpected offline RPC: {type(request).__name__}")

    def iter_messages(self, *args, **kwargs):
        return self.messages.iter_messages(*args, **kwargs)

    async def get_messages(self, *args, **kwargs):
        return await self.messages.get_messages(*args, **kwargs)


@pytest.fixture
def config_env(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(
        'default_account = "main"\n[accounts.main]\n'
        'api_id = 12345\napi_hash = "offline-placeholder"\n'
    )
    monkeypatch.setenv("TGCLI_CONFIG", str(path))
    monkeypatch.setenv("TGCLI_READONLY", "1")


COMMANDS = [
    ["resolve", "--json", "--", NAME],
    ["read", "--json", "--", NAME],
    ["search", "--json", "--", NAME, "needle"],
    ["info", "--json", "--", NAME],
]


def _request_kinds(client) -> list[str]:
    return [type(request).__name__ for request in client.requests]


@pytest.mark.parametrize("argv", COMMANDS, ids=lambda argv: argv[0])
def test_a_refused_channel_with_the_same_name_is_skipped(
    config_env, monkeypatch, capsys, tmp_path, argv
):
    client = SessionFileClient(
        tmp_path / "s", [_private_channel(), _user()], accessible=[_user()]
    )
    make_session_fake(monkeypatch, client)

    assert main(argv) == 0

    payload = json.loads(capsys.readouterr().out)
    resolved = payload.get("peer") or payload.get("dialog") or payload
    assert resolved["id"] == USER_ID
    assert _request_kinds(client) == ["GetChannelsRequest", "GetUsersRequest"]


@pytest.mark.parametrize("argv", COMMANDS, ids=lambda argv: argv[0])
def test_a_name_whose_every_peer_refuses_is_not_found(
    config_env, monkeypatch, capsys, tmp_path, argv
):
    client = SessionFileClient(tmp_path / "s", [_private_channel()], accessible=[])
    make_session_fake(monkeypatch, client)

    assert main(argv) == 4

    output = capsys.readouterr()
    assert json.loads(output.out)["error"]["code"] == "NOT_FOUND"
    assert _request_kinds(client) == ["GetChannelsRequest"]


@pytest.mark.asyncio
async def test_an_input_peer_by_name_is_the_peer_get_entity_picks(tmp_path):
    client = SessionFileClient(
        tmp_path / "s", [_private_channel(), _user()], accessible=[_user()]
    )

    peer = await client.get_input_entity(NAME)

    assert utils.get_peer_id(peer) == USER_ID
