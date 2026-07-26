"""Characterization (af-06): which peer-id form each read operation emits.

Two forms coexist on purpose. Listings built from a Telethon `Dialog` and the
global search carry Telegram's *marked* id (`-100…`), because that is what
`Dialog.id` / `Message.chat_id` are; every entity-based response carries the
bare `entity.id`. Changing either would be a breaking retype, so these tests
pin the current split against real `telethon.tl.types` objects — a permissive
fake would let the two drift into each other unnoticed.
"""

from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from telethon import utils
from telethon.tl import types
from telethon.tl.custom.dialog import Dialog
from telethon.tl.custom.message import Message

from tests.conftest import FakeClient
from tests.test_cli_media import _media_message
from tgcli import read_ops


CHANNEL_ID = 1234567890
MARKED_ID = -1001234567890


def make_channel() -> types.Channel:
    return types.Channel(
        id=CHANNEL_ID,
        title="Chan",
        photo=None,
        date=None,
        broadcast=True,
        access_hash=0,
        username="chan",
    )


def make_dialog(channel: types.Channel) -> Dialog:
    raw = types.Dialog(
        peer=types.PeerChannel(CHANNEL_ID),
        top_message=7,
        read_inbox_max_id=0,
        read_outbox_max_id=0,
        unread_count=2,
        unread_mentions_count=1,
        unread_reactions_count=0,
        unread_poll_votes_count=0,
        notify_settings=types.PeerNotifySettings(),
        draft=types.DraftMessageEmpty(),
    )
    return Dialog(None, raw, {utils.get_peer_id(raw.peer): channel}, None)


def make_message(channel: types.Channel) -> Message:
    message = Message(
        id=7,
        peer_id=types.PeerChannel(CHANNEL_ID),
        from_id=types.PeerChannel(CHANNEL_ID),
        date=datetime(2026, 7, 20, tzinfo=UTC),
        message="needle",
    )
    client = SimpleNamespace(_self_id=999, _mb_entity_cache={}, parse_mode=None)
    message._finish_init(client, {utils.get_peer_id(channel): channel}, None)
    return message


def test_the_two_id_forms_of_one_real_channel_differ():
    channel = make_channel()

    assert channel.id == CHANNEL_ID
    assert utils.get_peer_id(channel) == MARKED_ID


async def test_dialogs_emits_the_marked_id():
    channel = make_channel()
    client = FakeClient(dialogs=[make_dialog(channel)])

    result = await read_ops.execute(client, read_ops.Dialogs(50, False, None))

    assert [dialog["id"] for dialog in result.data["dialogs"]] == [MARKED_ID]


async def test_search_all_emits_the_marked_id_and_marked_sender():
    channel = make_channel()
    client = FakeClient(search_messages={"needle": [make_message(channel)]})

    result = await read_ops.execute(
        client, read_ops.Search(None, "needle", 20, True, None, None)
    )

    [message] = result.data["messages"]
    assert message["dialog"]["id"] == MARKED_ID
    assert message["from"]["id"] == MARKED_ID


@pytest.mark.parametrize(
    "operation, path",
    [
        (read_ops.Info("@chan", False), ("id",)),
        (read_ops.Count("@chan"), ("dialog", "id")),
        (read_ops.Search("@chan", "needle", 20, False, None, None), ("dialog", "id")),
        (read_ops.Latest("@chan"), ("dialog", "id")),
        (read_ops.Message("@chan", 7, 0), ("dialog", "id")),
        (read_ops.MediaManifest("@chan", None, None, 100), ("dialog", "id")),
        (
            read_ops.Read("@chan", 20, None, None, None, None, None),
            ("dialog", "id"),
        ),
    ],
    ids=lambda value: getattr(value, "name", ""),
)
async def test_entity_based_operations_emit_the_bare_id(operation, path):
    channel = make_channel()
    client = FakeClient(
        entities={"@chan": channel},
        messages=[_media_message(7, "photo")],
        search_messages={"needle": [_media_message(7, "photo")]},
        message_total=1,
    )

    data = (await read_ops.execute(client, operation)).data
    for key in path:
        data = data[key]

    assert data == CHANNEL_ID
