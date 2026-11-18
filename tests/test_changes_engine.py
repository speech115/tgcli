"""Change-feed engine tests: archive sync runs on this engine."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from telethon.tl.functions.updates import (
    GetDifferenceRequest,
    GetStateRequest,
)
from telethon.tl.types import (
    Channel,
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
    Difference,
    DifferenceTooLong,
    State,
)

from tgcli.archive import changes as changes_cmd
from tgcli.archive.changes_cursor import ChangesCursor


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
async def test_init_reads_only_the_common_state():
    async def handler(request, tg):
        assert isinstance(request, GetStateRequest)
        return _state(pts=5, qts=1, seq=2)

    tg = FakeTg(handler)
    cursor = await changes_cmd.init_changes(tg)
    assert (cursor.pts, cursor.qts, cursor.seq) == (5, 1, 2)
    assert len(tg.requests) == 1


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
async def test_difference_too_long_reports_it_and_jumps_to_the_current_state():
    async def handler(request, tg):
        if isinstance(request, GetDifferenceRequest):
            return DifferenceTooLong(pts=99)
        if isinstance(request, GetStateRequest):
            return _state(pts=100, qts=2, seq=9)
        raise AssertionError(type(request))

    tg = FakeTg(handler)
    cursor = ChangesCursor(pts=1, qts=0, date=0, seq=0)
    doc, new_cursor, requests = await changes_cmd.once(tg, cursor)
    assert doc["too_long"] is True
    assert doc["events"] == []
    assert new_cursor.pts == 100
    assert [type(r) for r in requests] == [GetDifferenceRequest, GetStateRequest]


@pytest.mark.asyncio
async def test_channel_updates_in_the_common_difference_are_reported():
    """No channel is polled on its own, so the common pass reports every
    channel message, edit and deletion it carries."""
    from telethon import utils

    channel = Channel(
        id=42,
        title="C",
        photo=None,
        date=datetime(2026, 1, 1, tzinfo=UTC),
        access_hash=7,
    )
    peer = utils.get_peer_id(channel)

    async def handler(request, tg):
        assert isinstance(request, GetDifferenceRequest)
        return Difference(
            new_messages=[],
            new_encrypted_messages=[],
            other_updates=[
                UpdateNewChannelMessage(
                    message=_msg(mid=1, peer=PeerChannel(42), text="new"),
                    pts=2,
                    pts_count=1,
                ),
                UpdateEditChannelMessage(
                    message=_msg(mid=1, peer=PeerChannel(42), text="edited"),
                    pts=3,
                    pts_count=1,
                ),
                UpdateDeleteChannelMessages(
                    channel_id=42, messages=[1], pts=4, pts_count=1
                ),
            ],
            chats=[channel],
            users=[],
            state=_state(pts=4),
        )

    doc, _, requests = await changes_cmd.once(
        FakeTg(handler), ChangesCursor(pts=1, qts=0, date=0, seq=0)
    )
    assert [(e["type"], e["peer"]) for e in doc["events"]] == [
        ("message_new", peer),
        ("message_edit", peer),
        ("message_delete", peer),
    ]
    assert len(requests) == 1
