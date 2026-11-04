"""Every command emits dialog ids in the form CONTRACT documents.

Entity-scoped shapes carry Telethon's positive `entity.id`; `tg dialogs` and
`tg inbox` carry the bot-API peer id (`-N` for a basic group, `-100…` for a
channel). Real Telethon entities drive the commands, so a `get_peer_id` or
`.id` swap in any of them fails here for the peer kind it breaks.
"""

from __future__ import annotations

import datetime as dt
import json

import pytest
from hypothesis import HealthCheck, given, settings, strategies as st
from telethon import utils
from telethon.tl import types
from telethon.tl.custom import Dialog

from tests.conftest import FakeClient, make_session_fake, ns
from tgcli.cli import main

SAMPLE = """
default_account = "main"

[accounts.main]
api_id = 12345
api_hash = "abcdef0123456789"
"""

DATE = dt.datetime(2026, 7, 6, 10, 0, tzinfo=dt.UTC)


def _user(peer_id: int):
    return types.User(id=peer_id, first_name="Peer", access_hash=1)


def _basic_group(peer_id: int):
    return types.Chat(
        id=peer_id,
        title="Peer",
        photo=types.ChatPhotoEmpty(),
        participants_count=2,
        date=DATE,
        version=1,
    )


def _channel(peer_id: int, **flags):
    return types.Channel(
        id=peer_id,
        title="Peer",
        photo=types.ChatPhotoEmpty(),
        date=DATE,
        access_hash=1,
        **flags,
    )


ENTITIES = st.builds(
    lambda make, peer_id: make(peer_id),
    st.sampled_from(
        [
            _user,
            _basic_group,
            lambda peer_id: _channel(peer_id, broadcast=True),
            lambda peer_id: _channel(peer_id, megagroup=True),
        ]
    ),
    st.integers(min_value=1, max_value=2**40),
)

# (argv, where the dialog id sits in the JSON)
ENTITY_SCOPED = [
    (["read", "@peer"], ("dialog", "id")),
    (["message", "@peer", "1"], ("dialog", "id")),
    (["count", "@peer"], ("dialog", "id")),
    (["search", "@peer", "hello"], ("dialog", "id")),
    (["thread", "@peer", "1"], ("dialog", "id")),
    (["info", "@peer"], ("id",)),
    (["export", "messages", "@peer", "--output", "OUT"], ("export", "dialog", "id")),
]


@pytest.fixture
def config_env(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE)
    monkeypatch.setenv("TGCLI_CONFIG", str(path))


def _message(entity):
    return ns(
        id=1,
        date=DATE,
        sender_id=111,
        sender=ns(first_name="Alice", last_name=None),
        text="hello",
        media=None,
        reply_to_msg_id=None,
        chat_id=utils.get_peer_id(entity),
        chat=entity,
    )


def _run(argv: list[str], capsys) -> dict:
    capsys.readouterr()
    assert main(["--json", *argv]) == 0
    return json.loads(capsys.readouterr().out)


@settings(max_examples=30, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(entity=ENTITIES)
@pytest.mark.parametrize(
    ("argv", "keys"), ENTITY_SCOPED, ids=[argv[0] for argv, _ in ENTITY_SCOPED]
)
def test_entity_scoped_commands_emit_the_positive_entity_id(
    argv, keys, entity, config_env, monkeypatch, capsys, tmp_path
):
    message = _message(entity)
    fake = FakeClient(
        entities={"@peer": entity},
        messages=[message],
        search_messages={"hello": [message]},
        message_total=1,
    )
    make_session_fake(monkeypatch, fake)
    output = tmp_path / "export.jsonl"
    output.unlink(missing_ok=True)

    emitted = _run([str(output) if arg == "OUT" else arg for arg in argv], capsys)
    for key in keys:
        emitted = emitted[key]

    assert emitted == entity.id > 0


@settings(max_examples=30, suppress_health_check=[HealthCheck.function_scoped_fixture])
@given(entity=ENTITIES)
def test_dialogs_emit_the_bot_api_peer_id(entity, config_env, monkeypatch, capsys):
    peer = utils.get_peer(entity)
    raw = types.Dialog(
        peer=peer,
        top_message=0,
        read_inbox_max_id=0,
        read_outbox_max_id=0,
        unread_count=0,
        unread_mentions_count=0,
        unread_reactions_count=0,
        unread_poll_votes_count=0,
        notify_settings=types.PeerNotifySettings(),
    )
    dialog = Dialog(None, raw, {utils.get_peer_id(peer): entity}, None)
    make_session_fake(monkeypatch, FakeClient(dialogs=[dialog]))

    data = _run(["dialogs"], capsys)

    assert [row["id"] for row in data["dialogs"]] == [utils.get_peer_id(entity)]
