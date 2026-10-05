"""Change-feed engine tests (ADR-0063): archive sync runs on this engine."""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.updates import (
    GetChannelDifferenceRequest,
    GetDifferenceRequest,
    GetStateRequest,
)
from telethon.tl.types import (
    Channel,
    ChannelMessagesFilterEmpty,
    InputChannel,
    Message,
    PeerChannel,
    PeerUser,
    UpdateChannelTooLong,
    UpdateDeleteChannelMessages,
    UpdateEditChannelMessage,
    UpdateNewChannelMessage,
    UpdateNewMessage,
)
from telethon.tl.types.updates import (
    ChannelDifference,
    ChannelDifferenceEmpty,
    ChannelDifferenceTooLong,
    Difference,
    DifferenceEmpty,
    DifferenceTooLong,
    State,
)

from tgcli import changes_cursor
from tgcli.changes_cursor import ChangesCursor
from tgcli.commands import changes as changes_cmd


def _state(pts=10, qts=1, seq=2, date=None):
    return State(
        pts=pts,
        qts=qts,
        date=date or datetime(2026, 1, 1, tzinfo=UTC),
        seq=seq,
        unread_count=0,
    )


def _msg(*, mid=1, peer=None, text="hi", edit_date=None):
    return Message(
        id=mid,
        peer_id=peer or PeerUser(9),
        message=text,
        date=datetime(2026, 1, 2, tzinfo=UTC),
        edit_date=edit_date,
    )


class FakeTg:
    def __init__(self, handler):
        self.handler = handler
        self.requests = []
        self.entities = {}

    async def __call__(self, request):
        self.requests.append(request)
        return await self.handler(request, self)

    async def get_entity(self, ref):
        key = ref if not isinstance(ref, int) else ref
        if key not in self.entities and isinstance(ref, int):
            # marked peer → channel
            from telethon import utils

            real, peer_type = utils.resolve_id(ref)
            if peer_type is PeerChannel:
                entity = Channel(
                    id=real,
                    title="C",
                    photo=None,
                    date=datetime(2026, 1, 1, tzinfo=UTC),
                    access_hash=1,
                )
                self.entities[ref] = entity
                self.entities[f"@{real}"] = entity
        if key not in self.entities:
            raise ValueError(ref)
        return self.entities[key]

    async def get_input_entity(self, entity):
        return InputChannel(entity.id, entity.access_hash or 0)


@pytest.mark.asyncio
async def test_init_get_state_and_channel_full_pts():
    channel = Channel(
        id=1234,
        title="News",
        photo=None,
        date=datetime(2026, 1, 1, tzinfo=UTC),
        access_hash=99,
    )

    async def handler(request, tg):
        if isinstance(request, GetStateRequest):
            return _state(pts=5, qts=1, seq=2)
        if isinstance(request, GetFullChannelRequest):
            return SimpleNamespace(full_chat=SimpleNamespace(pts=77))
        raise AssertionError(type(request))

    tg = FakeTg(handler)
    tg.entities["@news"] = channel
    data = await changes_cmd.init_changes(tg, ["@news"])
    assert data["events"] == []
    assert data["gap"] is None
    cursor = changes_cursor.decode(data["next_cursor"])
    assert cursor.pts == 5 and cursor.qts == 1 and cursor.seq == 2
    assert len(cursor.channels) == 1
    peer = next(iter(cursor.channels))
    assert cursor.channels[peer] == 77
    assert any(isinstance(r, GetStateRequest) for r in tg.requests)
    assert any(isinstance(r, GetFullChannelRequest) for r in tg.requests)


@pytest.mark.asyncio
async def test_common_difference_maps_events_and_request_types():
    async def handler(request, tg):
        if isinstance(request, GetDifferenceRequest):
            assert request.pts == 5
            assert request.qts == 1
            assert request.pts_total_limit == changes_cmd.PTS_TOTAL_LIMIT
            assert isinstance(request.date, datetime)
            return Difference(
                new_messages=[_msg(mid=7, peer=PeerUser(9), text="hello")],
                new_encrypted_messages=[],
                other_updates=[
                    UpdateNewMessage(
                        message=_msg(mid=8, peer=PeerUser(9), text="u"),
                        pts=6,
                        pts_count=1,
                    ),
                    UpdateChannelTooLong(channel_id=555, pts=None),
                ],
                chats=[],
                users=[],
                state=_state(pts=6, qts=1, seq=3),
            )
        raise AssertionError(type(request))

    tg = FakeTg(handler)
    cursor = ChangesCursor(pts=5, qts=1, date=0, seq=2)
    doc, new_cursor, requests = await changes_cmd.once(tg, cursor)
    assert len(requests) == 1
    assert isinstance(requests[0], GetDifferenceRequest)
    assert requests[0].pts_total_limit == 100_000
    types_seen = {e["type"] for e in doc["events"]}
    assert "message_new" in types_seen
    assert "channel_activity" in types_seen
    activity = next(e for e in doc["events"] if e["type"] == "channel_activity")
    assert activity["peer"] < 0
    assert new_cursor.pts == 6


@pytest.mark.asyncio
async def test_difference_too_long_emits_gap_and_rebases():
    calls = {"n": 0}

    async def handler(request, tg):
        if isinstance(request, GetDifferenceRequest):
            return DifferenceTooLong(pts=99)
        if isinstance(request, GetStateRequest):
            calls["n"] += 1
            return _state(pts=100, qts=2, seq=9)
        raise AssertionError(type(request))

    tg = FakeTg(handler)
    cursor = ChangesCursor(pts=1, qts=0, date=0, seq=0)
    doc, new_cursor, requests = await changes_cmd.once(tg, cursor)
    assert doc["gap"]["scope"] == "common"
    assert doc["gap"]["reason"] == "differenceTooLong"
    assert doc["gap"]["recover"]["edits_deletes"] == "lost"
    assert new_cursor.pts == 100
    assert isinstance(requests[0], GetDifferenceRequest)
    assert isinstance(requests[1], GetStateRequest)


@pytest.mark.asyncio
async def test_channel_difference_request_shape_and_events():
    channel = Channel(
        id=42,
        title="C",
        photo=None,
        date=datetime(2026, 1, 1, tzinfo=UTC),
        access_hash=7,
    )
    from telethon import utils

    peer = utils.get_peer_id(channel)

    async def handler(request, tg):
        if isinstance(request, GetDifferenceRequest):
            return DifferenceEmpty(date=datetime(2026, 1, 1, tzinfo=UTC), seq=1)
        if isinstance(request, GetChannelDifferenceRequest):
            assert isinstance(request.channel, InputChannel)
            assert isinstance(request.filter, ChannelMessagesFilterEmpty)
            assert request.pts == 3
            assert request.limit == changes_cmd.CHANNEL_DIFF_LIMIT
            return ChannelDifference(
                pts=8,
                new_messages=[
                    _msg(mid=11, peer=PeerChannel(42), text="chan"),
                ],
                other_updates=[
                    UpdateEditChannelMessage(
                        message=_msg(
                            mid=11,
                            peer=PeerChannel(42),
                            text="edited",
                            edit_date=datetime(2026, 1, 3, tzinfo=UTC),
                        ),
                        pts=9,
                        pts_count=1,
                    ),
                    UpdateDeleteChannelMessages(
                        channel_id=42, messages=[10], pts=10, pts_count=1
                    ),
                ],
                chats=[channel],
                users=[],
                final=True,
            )
        raise AssertionError(type(request))

    tg = FakeTg(handler)
    tg.entities[peer] = channel
    cursor = ChangesCursor(pts=1, qts=0, date=0, seq=1, channels={peer: 3})
    doc, new_cursor, requests = await changes_cmd.once(tg, cursor)
    assert any(isinstance(r, GetChannelDifferenceRequest) for r in requests)
    ch_req = next(r for r in requests if isinstance(r, GetChannelDifferenceRequest))
    assert ch_req.limit == 100
    kinds = [e["type"] for e in doc["events"]]
    assert "message_new" in kinds
    assert "message_edit" in kinds
    assert "message_delete" in kinds
    delete = next(e for e in doc["events"] if e["type"] == "message_delete")
    assert delete["ids"] == [10]
    assert new_cursor.channels[peer] == 8


@pytest.mark.asyncio
async def test_subscribed_channel_not_double_reported_from_common():
    channel = Channel(
        id=42,
        title="C",
        photo=None,
        date=datetime(2026, 1, 1, tzinfo=UTC),
        access_hash=7,
    )
    from telethon import utils

    peer = utils.get_peer_id(channel)

    async def handler(request, tg):
        if isinstance(request, GetDifferenceRequest):
            return Difference(
                new_messages=[],
                new_encrypted_messages=[],
                other_updates=[
                    UpdateNewChannelMessage(
                        message=_msg(mid=1, peer=PeerChannel(42), text="dup"),
                        pts=2,
                        pts_count=1,
                    )
                ],
                chats=[channel],
                users=[],
                state=_state(pts=2),
            )
        if isinstance(request, GetChannelDifferenceRequest):
            return ChannelDifferenceEmpty(pts=3, final=True)
        raise AssertionError(type(request))

    tg = FakeTg(handler)
    tg.entities[peer] = channel
    cursor = ChangesCursor(pts=1, qts=0, date=0, seq=0, channels={peer: 3})
    doc, _, _ = await changes_cmd.once(tg, cursor)
    assert doc["events"] == []


@pytest.mark.asyncio
async def test_channel_too_long_gap():
    channel = Channel(
        id=42,
        title="C",
        photo=None,
        date=datetime(2026, 1, 1, tzinfo=UTC),
        access_hash=7,
    )
    from telethon import utils

    peer = utils.get_peer_id(channel)

    async def handler(request, tg):
        if isinstance(request, GetDifferenceRequest):
            return DifferenceEmpty(date=datetime(2026, 1, 1, tzinfo=UTC), seq=1)
        if isinstance(request, GetChannelDifferenceRequest):
            dialog = SimpleNamespace(pts=50)
            return ChannelDifferenceTooLong(
                dialog=dialog, messages=[], chats=[], users=[], final=True
            )
        raise AssertionError(type(request))

    tg = FakeTg(handler)
    tg.entities[peer] = channel
    cursor = ChangesCursor(pts=1, qts=0, date=0, seq=1, channels={peer: 1})
    doc, new_cursor, _ = await changes_cmd.once(tg, cursor)
    assert doc["gap"]["scope"] == peer
    assert doc["gap"]["reason"] == "channelDifferenceTooLong"
    assert new_cursor.channels[peer] == 50


def _fake_session(monkeypatch, tg):
    from contextlib import asynccontextmanager

    from tgcli import session

    @asynccontextmanager
    async def fake_session(account, *, mutation_safe=False, role=None, govern=True):
        yield tg

    monkeypatch.setattr(session, "client", fake_session)


def _forge_channels(cursor: str, channels: dict[int, int]) -> str:
    prefix, encoded = cursor.split(":", 1)
    padded = encoded + "=" * (-len(encoded) % 4)
    payload = json.loads(base64.urlsafe_b64decode(padded).decode())
    payload["channels"] = {str(peer): pts for peer, pts in channels.items()}
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return prefix + ":" + base64.urlsafe_b64encode(raw).decode().rstrip("=")
