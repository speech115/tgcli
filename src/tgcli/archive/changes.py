"""The common update cursor behind archive sync.

One ``updates.getDifference`` pass per run brings new and edited messages and,
above all, deletions in private chats, which arrive without a peer and only
through this cursor. Channels are not polled one by one: a channel that falls
behind shows up as ``channel_activity`` and archive sync reads it by
``min_id``. When Telegram answers ``differenceTooLong`` the cursor jumps to
the current state and the pass reports ``too_long``; archive sync then finds
the chats that moved through a dialog poll.
"""

from __future__ import annotations

from datetime import UTC, datetime

from telethon import errors as telethon_errors, utils
from telethon.tl import types
from telethon.tl.functions.updates import (
    GetDifferenceRequest,
    GetStateRequest,
)
from telethon.tl.types import (
    MessageEmpty,
    UpdateChannelTooLong,
    UpdateDeleteChannelMessages,
    UpdateDeleteMessages,
    UpdateEditChannelMessage,
    UpdateEditMessage,
    UpdateNewChannelMessage,
    UpdateNewMessage,
)
from telethon.tl.types.updates import (
    Difference,
    DifferenceEmpty,
    DifferenceSlice,
    DifferenceTooLong,
)

from tgcli.archive.changes_cursor import ChangesCursor
from tgcli.messages import message_to_dict

PTS_TOTAL_LIMIT = 100_000


def _peer_id(entity_or_peer) -> int:
    return int(utils.get_peer_id(entity_or_peer))


def _unix(dt) -> int:
    if dt is None:
        return 0
    if isinstance(dt, (int, float)):
        return int(dt)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=UTC)
    return int(dt.timestamp())


def _from_state(state) -> ChangesCursor:
    return ChangesCursor(
        pts=int(state.pts),
        qts=int(state.qts),
        date=_unix(state.date),
        seq=int(state.seq),
    )


def _bump_skipped(skipped: dict[str, int], name: str, n: int = 1) -> None:
    skipped[name] = skipped.get(name, 0) + n


def _message_event(kind: str, message, *, chats) -> dict:
    peer = (
        _peer_id(message.peer_id)
        if getattr(message, "peer_id", None) is not None
        else None
    )
    if isinstance(message, MessageEmpty) or not hasattr(message, "date"):
        return {
            "type": kind,
            "peer": peer,
            "message": {"id": getattr(message, "id", None)},
            "truncated": True,
        }
    entity = None
    if peer is not None:
        for chat in chats or ():
            try:
                if _peer_id(chat) == peer:
                    entity = chat
                    break
            except Exception:
                continue
    return {
        "type": kind,
        "peer": peer,
        "message": message_to_dict(message, entity),
        "truncated": False,
    }


def _map_update(
    update,
    *,
    chats,
    skipped: dict,
    private_deletes: bool = False,
) -> list[dict]:
    if isinstance(update, (UpdateNewMessage, UpdateNewChannelMessage)):
        return [_message_event("message_new", update.message, chats=chats)]
    if isinstance(update, (UpdateEditMessage, UpdateEditChannelMessage)):
        return [_message_event("message_edit", update.message, chats=chats)]
    if isinstance(update, UpdateDeleteChannelMessages):
        peer = _peer_id(types.PeerChannel(update.channel_id))
        return [{"type": "message_delete", "peer": peer, "ids": list(update.messages)}]
    if isinstance(update, UpdateDeleteMessages):
        if private_deletes:
            return [
                {"type": "message_delete", "peer": None, "ids": list(update.messages)}
            ]
        _bump_skipped(skipped, type(update).__name__, len(update.messages) or 1)
        return []
    if isinstance(update, UpdateChannelTooLong):
        peer = _peer_id(types.PeerChannel(update.channel_id))
        return [{"type": "channel_activity", "peer": peer}]
    _bump_skipped(skipped, type(update).__name__)
    return []


def _map_difference_events(
    result,
    *,
    skipped: dict,
    private_deletes: bool = False,
) -> list[dict]:
    chats = list(result.chats or ())
    events = [
        _message_event("message_new", message, chats=chats)
        for message in result.new_messages or ()
    ]
    for update in result.other_updates or ():
        events.extend(
            _map_update(
                update,
                chats=chats,
                skipped=skipped,
                private_deletes=private_deletes,
            )
        )
    return events


async def _poll_common(
    tg, cursor: ChangesCursor, *, skipped: dict, private_deletes: bool = False
) -> tuple[list[dict], ChangesCursor, bool, list]:
    events: list[dict] = []
    too_long = False
    requests: list = []
    pts, qts, date, seq = cursor.pts, cursor.qts, cursor.date, cursor.seq
    while True:
        request = GetDifferenceRequest(
            pts=pts,
            date=datetime.fromtimestamp(date, tz=UTC),
            qts=qts,
            pts_total_limit=PTS_TOTAL_LIMIT,
        )
        requests.append(request)
        result = await tg(request)
        if isinstance(result, DifferenceEmpty):
            date = _unix(result.date)
            seq = int(result.seq)
            break
        if isinstance(result, DifferenceTooLong):
            too_long = True
            state_req = GetStateRequest()
            requests.append(state_req)
            state = await tg(state_req)
            pts, qts, date, seq = (
                int(state.pts),
                int(state.qts),
                _unix(state.date),
                int(state.seq),
            )
            break
        if isinstance(result, (Difference, DifferenceSlice)):
            events.extend(
                _map_difference_events(
                    result,
                    skipped=skipped,
                    private_deletes=private_deletes,
                )
            )
            common_state = (
                result.state
                if isinstance(result, Difference)
                else result.intermediate_state
            )
            pts = int(common_state.pts)  # type: ignore[union-attr]
            qts = int(common_state.qts)  # type: ignore[union-attr]
            date = _unix(common_state.date)  # type: ignore[union-attr]
            seq = int(common_state.seq)  # type: ignore[union-attr]
            if isinstance(result, Difference):
                break
            continue
        _bump_skipped(skipped, type(result).__name__)
        break
    return (
        events,
        ChangesCursor(pts=pts, qts=qts, date=date, seq=seq),
        too_long,
        requests,
    )


# What Telegram says about a channel this account can no longer read.
CHANNEL_LOST_ERRORS = (
    telethon_errors.ChannelPrivateError,
    telethon_errors.ChannelInvalidError,
    telethon_errors.ChannelBannedError,
)


async def once(
    tg, cursor: ChangesCursor, *, private_deletes: bool = False
) -> tuple[dict, ChangesCursor, list]:
    """One difference pass. Returns document body, cursor, requests made.

    When ``private_deletes`` is true, ``UpdateDeleteMessages`` becomes
    ``message_delete`` events with ``peer: null`` (archive resolves peers
    from the local store). Without it they are skipped.
    """
    skipped: dict[str, int] = {}
    events, cursor, too_long, requests = await _poll_common(
        tg, cursor, skipped=skipped, private_deletes=private_deletes
    )
    return (
        {"events": events, "too_long": too_long, "skipped": skipped},
        cursor,
        requests,
    )


async def init_changes(tg) -> ChangesCursor:
    return _from_state(await tg(GetStateRequest()))
