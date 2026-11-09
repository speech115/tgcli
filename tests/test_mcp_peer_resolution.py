"""MCP backing reads reject cross-type resolution from real Telethon caches."""

import json

import pytest
from telethon import TelegramClient, utils
from telethon.sessions import MemorySession
from telethon.tl import functions, types

from tests.conftest import FakeClient, make_session_fake, ns
from tests.fakes.messages import DATE, message
from tgcli.cli import main

PEER_ID = 1234567890
CHANNEL_ID = -1001234567890
COMMANDS = [
    ("read", []),
    ("search", ["needle"]),
    ("message", ["7"]),
    ("message", ["7", "--context", "1"]),
    ("info", []),
    ("info", ["--full"]),
    ("latest", []),
    ("count", []),
]


class OfflineClient(TelegramClient):
    """Keep real entity resolution; stub only Telegram RPCs/message reads."""

    def __init__(self, entity):
        super().__init__(MemorySession(), 1, "offline-placeholder")
        self.entity = entity
        self.requests = []
        self.messages = FakeClient(
            messages=[message(7, entity, message="needle")], message_total=1
        )
        users = [entity] if isinstance(entity, types.User) else []
        chats = [] if users else [entity]
        self._mb_entity_cache.extend(users, chats)
        self.session.process_entities(types.contacts.ResolvedPeer(None, chats, users))

    async def connect(self):
        raise AssertionError("No network connection allowed")

    async def __call__(self, request, *args, **kwargs):
        self.requests.append(request)
        if isinstance(request, functions.users.GetUsersRequest):
            assert isinstance(self.entity, types.User)
            assert request.id[0].user_id == self.entity.id
            return [self.entity]
        if isinstance(request, functions.channels.GetChannelsRequest):
            assert isinstance(self.entity, types.Channel)
            assert request.id[0].channel_id == self.entity.id
            return types.messages.Chats(chats=[self.entity])
        if isinstance(request, functions.messages.GetChatsRequest):
            assert isinstance(self.entity, types.Chat)
            assert request.id == [self.entity.id]
            return types.messages.Chats(chats=[self.entity])
        if isinstance(request, functions.contacts.ResolveUsernameRequest):
            assert request.username == "fixture"
            return types.contacts.ResolvedPeer(
                utils.get_peer(self.entity), [], [self.entity]
            )
        if isinstance(request, functions.channels.GetFullChannelRequest):
            return ns(full_chat=ns(about="Fixture"))
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


def _argv(command, tail, peer):
    # Options precede -- so marked negative peer IDs remain positional.
    options = ["--json"]
    positional = []
    if "--context" in tail:
        options += ["--context", "1"]
        positional = ["7"]
    elif "--full" in tail:
        options += ["--full"]
    else:
        positional = tail
    return [command, *options, "--", str(peer), *positional]


@pytest.mark.parametrize("command,tail", COMMANDS)
@pytest.mark.parametrize(
    "cached_kind,peer",
    [("user", CHANNEL_ID), ("user", -PEER_ID), ("channel", -PEER_ID)],
)
def test_reads_reject_cached_wrong_type_for_marked_peer(
    config_env, monkeypatch, capsys, command, tail, cached_kind, peer
):
    if cached_kind == "user":
        entity = types.User(PEER_ID, access_hash=42, first_name="Private")
        request_type = functions.users.GetUsersRequest
    else:
        entity = types.Channel(
            PEER_ID,
            "Private",
            types.ChatPhotoEmpty(),
            DATE,
            access_hash=42,
            broadcast=True,
        )
        request_type = functions.channels.GetChannelsRequest
    client = OfflineClient(entity)
    make_session_fake(monkeypatch, client)

    code = main(_argv(command, tail, peer))

    output = capsys.readouterr()
    assert code == 4
    expected = {
        "error": {"code": "NOT_FOUND", "message": f"dialog not found: {str(peer)!r}"}
    }
    assert json.loads(output.out) == json.loads(output.err) == expected
    assert not client.messages.iter_messages_calls
    assert not client.messages.get_messages_calls
    assert len(client.requests) == 1
    assert isinstance(client.requests[0], request_type)
    assert not client.is_connected()


@pytest.mark.parametrize("command,tail", COMMANDS)
@pytest.mark.parametrize(
    "kind", ["channel", "group", "username", "positive", "positive_channel"]
)
def test_reads_keep_matching_peers_and_existing_reference_forms(
    config_env, monkeypatch, capsys, command, tail, kind
):
    if kind in ("channel", "positive_channel"):
        entity = types.Channel(
            PEER_ID,
            "Fixture",
            types.ChatPhotoEmpty(),
            DATE,
            access_hash=42,
            broadcast=True,
        )
        peer = PEER_ID if kind == "positive_channel" else CHANNEL_ID
    elif kind == "group":
        entity = types.Chat(PEER_ID, "Fixture", types.ChatPhotoEmpty(), 1, DATE, 1)
        peer = -PEER_ID
    else:
        entity = types.User(PEER_ID, access_hash=42, first_name="Fixture")
        peer = "@fixture" if kind == "username" else PEER_ID
    client = OfflineClient(entity)
    make_session_fake(monkeypatch, client)

    assert main(_argv(command, tail, peer)) == 0

    output = capsys.readouterr()
    payload = json.loads(output.out)
    assert payload.get("dialog", payload)["id"] == PEER_ID
    assert not client.is_connected()
