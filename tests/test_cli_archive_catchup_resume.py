"""`tg archive sync` resumes unfinished channel catch-ups without new activity.

A catch-up cut by its page limit, the message budget, or `--max-dialogs`
reports `remaining: true`; the next `archive sync` must finish it even when
the difference pass carries no new `channel_activity` for that channel.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime

from telethon import utils
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.updates import GetDifferenceRequest, GetStateRequest
from telethon.tl.types import (
    Channel,
    Message,
    PeerChannel,
    UpdateChannelTooLong,
    User,
)
from telethon.tl.types.updates import Difference, DifferenceEmpty, State

from tests.conftest import FakeClient, make_session_fake
from tgcli import changes_cursor
from tgcli.archive import store as store_mod
from tgcli.changes_cursor import ChangesCursor
from tgcli.cli import main
from tgcli.commands import archive as archive_cmd

SAMPLE = """
default_account = "main"

[accounts.main]
api_id = 12345
api_hash = "abcdef0123456789"
session = "main"
"""


def _setup_config(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE)
    monkeypatch.setenv("TGCLI_CONFIG", str(path))
    state = tmp_path / "state"
    monkeypatch.setenv("TGCLI_STATE_DIR", str(state))
    (state / "sessions").mkdir(parents=True, exist_ok=True)
    (state / "sessions" / "main.session").write_bytes(b"x")


def _state(pts: int) -> State:
    return State(
        pts=pts,
        qts=1,
        date=datetime(2026, 1, 1, tzinfo=UTC),
        seq=1,
        unread_count=0,
    )


def _channel(channel_id: int, username: str) -> Channel:
    return Channel(
        id=channel_id,
        title=username.title(),
        photo=None,
        date=datetime(2026, 1, 1, tzinfo=UTC),
        access_hash=99,
        megagroup=False,
        username=username,
    )


def _messages(channel_id: int, ids: range) -> list[Message]:
    # Newest first, as Telegram returns history.
    return [
        Message(
            id=mid,
            peer_id=PeerChannel(channel_id),
            message=f"m{mid}",
            date=datetime(2026, 1, 4, tzinfo=UTC),
            out=False,
        )
        for mid in sorted(ids, reverse=True)
    ]


class OneActivityClient(FakeClient):
    """First difference reports activity in `channels`; later ones are empty."""

    def __init__(self, *, channels: list[Channel], per_channel, **kwargs):
        super().__init__(**kwargs)
        self.activity = channels
        self.per_channel = per_channel
        self.differences = 0

    async def __call__(self, request):
        self.call_requests.append(request)
        if isinstance(request, GetFullChannelRequest):
            # Skip subscription so UpdateChannelTooLong reaches the common tier.
            raise RuntimeError("pts unavailable")
        if isinstance(request, GetStateRequest):
            return _state(5)
        if isinstance(request, GetDifferenceRequest):
            self.differences += 1
            if self.differences == 1:
                return Difference(
                    new_messages=[],
                    new_encrypted_messages=[],
                    other_updates=[
                        UpdateChannelTooLong(channel_id=channel.id, pts=None)
                        for channel in self.activity
                    ],
                    chats=list(self.activity),
                    users=[],
                    state=_state(6),
                )
            return DifferenceEmpty(date=datetime(2026, 1, 5, tzinfo=UTC), seq=1)
        raise AssertionError(request)

    async def iter_messages(self, entity, *args, **kwargs):
        self._messages = self.per_channel[entity.id]
        async for message in super().iter_messages(entity, *args, **kwargs):
            yield message


def _start(tmp_path, monkeypatch, capsys, channels, per_channel):
    _setup_config(tmp_path, monkeypatch)
    entities = {}
    for channel in channels:
        entities[utils.get_peer_id(channel)] = channel
        entities[channel.id] = channel
        entities[f"@{channel.username}"] = channel
    client = OneActivityClient(
        channels=channels,
        per_channel=per_channel,
        me=User(id=42, is_self=True, access_hash=1, first_name="Me", phone="100"),
        entities=entities,
    )
    make_session_fake(monkeypatch, client)
    assert main(["archive", "init", "--json"]) == 0
    capsys.readouterr()
    conn = store_mod.connect(archive_cmd.db_path("main"))
    try:
        for channel in channels:
            peer = utils.get_peer_id(channel)
            store_mod.add_scope(
                conn,
                peer_id=peer,
                kind="channel",
                title=channel.title,
                username=channel.username,
                chat_ref=f"@{channel.username}",
            )
            store_mod.upsert_sync_state(
                conn,
                peer,
                oldest_id=1,
                newest_id=5,
                more=False,
                kind="channel",
                title=channel.title,
                username=channel.username,
                chat_ref=f"@{channel.username}",
            )
        cursor = ChangesCursor(pts=5, qts=1, date=1_735_689_600, seq=1, channels={})
        store_mod.write_account_sync(conn, changes_cursor=changes_cursor.encode(cursor))
    finally:
        conn.close()
    return client


def _sync(capsys, *extra: str) -> dict:
    assert main(["archive", "sync", "--json", *extra]) == 0
    return json.loads(capsys.readouterr().out)


def _stored_ids(peer: int) -> list[int]:
    conn = store_mod.connect(archive_cmd.db_path("main"))
    try:
        rows = conn.execute(
            "SELECT message_id FROM messages WHERE peer_id = ? ORDER BY message_id",
            (peer,),
        ).fetchall()
    finally:
        conn.close()
    return [int(row["message_id"]) for row in rows]


def test_catchup_cut_by_its_page_limit_resumes_without_new_activity(
    tmp_path, monkeypatch, capsys
):
    news = _channel(1234, "news")
    peer = utils.get_peer_id(news)
    _start(
        tmp_path,
        monkeypatch,
        capsys,
        [news],
        {news.id: _messages(news.id, range(6, 9))},
    )

    first = _sync(capsys, "--max-events", "2")
    assert first["catchups"] == [{"peer_id": peer, "stored": 2, "more": True}]
    assert first["remaining"] is True
    assert _stored_ids(peer) == [6, 7]

    second = _sync(capsys, "--max-events", "2")
    assert second["applied"]["channel_activity"] == 0
    assert second["catchups"] == [{"peer_id": peer, "stored": 1, "more": False}]
    assert second["remaining"] is False
    assert _stored_ids(peer) == [6, 7, 8]

    third = _sync(capsys, "--max-events", "2")
    assert third["catchups"] == []
    assert third["remaining"] is False


def test_catchup_skipped_by_max_dialogs_resumes_without_new_activity(
    tmp_path, monkeypatch, capsys
):
    news = _channel(1234, "news")
    blog = _channel(5678, "blog")
    news_peer = utils.get_peer_id(news)
    blog_peer = utils.get_peer_id(blog)
    _start(
        tmp_path,
        monkeypatch,
        capsys,
        [news, blog],
        {
            news.id: _messages(news.id, range(6, 7)),
            blog.id: _messages(blog.id, range(6, 7)),
        },
    )

    first = _sync(capsys, "--max-dialogs", "1")
    assert [item["peer_id"] for item in first["catchups"]] == [news_peer]
    assert first["remaining"] is True

    second = _sync(capsys, "--max-dialogs", "1")
    assert second["catchups"] == [{"peer_id": blog_peer, "stored": 1, "more": False}]
    assert second["remaining"] is False
    assert _stored_ids(blog_peer) == [6]


def test_unfinished_catchup_of_a_removed_channel_is_dropped(
    tmp_path, monkeypatch, capsys
):
    news = _channel(1234, "news")
    peer = utils.get_peer_id(news)
    client = _start(
        tmp_path,
        monkeypatch,
        capsys,
        [news],
        {news.id: _messages(news.id, range(6, 9))},
    )

    first = _sync(capsys, "--max-events", "2")
    assert first["remaining"] is True

    assert main(["archive", "remove", "@news", "--json"]) == 0
    capsys.readouterr()
    calls = len(client.iter_messages_calls)

    second = _sync(capsys, "--max-events", "2")
    assert second["catchups"] == []
    assert second["remaining"] is False
    assert len(client.iter_messages_calls) == calls
    assert _stored_ids(peer) == [6, 7]
