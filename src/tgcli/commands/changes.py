"""Daemonless change feed: `tg changes` (ADR-0063 / ADR-0103 / FEED-001)."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from telethon import utils
from telethon.tl import types
from telethon.tl.functions.channels import GetFullChannelRequest
from telethon.tl.functions.updates import (
    GetChannelDifferenceRequest,
    GetDifferenceRequest,
    GetStateRequest,
)
from telethon.tl.types import (
    Channel,
    ChannelMessagesFilterEmpty,
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
    ChannelDifference,
    ChannelDifferenceEmpty,
    ChannelDifferenceTooLong,
    Difference,
    DifferenceEmpty,
    DifferenceSlice,
    DifferenceTooLong,
)

from tgcli import changes_cursor, chatref
from tgcli.changes_cursor import ChangesCursor
from tgcli.commands.read import message_to_dict
from tgcli.errors import ConfigError, NotFoundError, PolicyError
from tgcli.output import note

SETTLE_SECONDS = 2.0
CHANNEL_DIFF_LIMIT = 100
PTS_TOTAL_LIMIT = 100_000
POLL_SLEEP_S = 0.5

# Injectable clock/sleep for --wait tests.
_monotonic: Callable[[], float] | None = None
_sleep: Callable[[float], Awaitable[None]] | None = None


def _now() -> float:
    if _monotonic is not None:
        return float(_monotonic())
    import time

    return time.monotonic()


async def _async_sleep(seconds: float) -> None:
    if _sleep is not None:
        await _sleep(seconds)
        return
    import asyncio

    await asyncio.sleep(seconds)


def to_rows(data: dict) -> list[tuple]:
    gap = data.get("gap")
    gap_scope = None if gap is None else gap.get("scope")
    skipped = data.get("skipped") or {}
    return [
        (
            len(data.get("events") or []),
            data.get("next_cursor"),
            gap_scope,
            sum(int(v) for v in skipped.values()),
        )
    ]


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
        channels={},
    )


async def _resolve_channel(tg, ref: str):
    try:
        entity = await tg.get_entity(chatref.parse(ref))
    except ValueError as exc:
        raise NotFoundError(f"dialog not found: {ref!r}") from exc
    if not isinstance(entity, Channel):
        raise PolicyError(
            f"{ref!r} is not a channel/supergroup; "
            "only channels are subscribed — private dialogs use the common tier"
        )
    return entity


async def _channel_pts(tg, entity) -> int:
    """Baseline at the channel's current pts (no history replay)."""
    full = await tg(GetFullChannelRequest(channel=entity))
    pts = getattr(full.full_chat, "pts", None)
    if not isinstance(pts, int) or pts < 0:
        raise ConfigError(f"channel {entity.id} did not report a pts baseline")
    return pts


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
    subscribed: set[int],
    chats,
    skipped: dict,
    private_deletes: bool = False,
) -> list[dict]:
    events: list[dict] = []
    if isinstance(update, (UpdateNewMessage, UpdateNewChannelMessage)):
        message = update.message
        peer = (
            _peer_id(message.peer_id)
            if getattr(message, "peer_id", None) is not None
            else None
        )
        if (
            isinstance(update, UpdateNewChannelMessage)
            and peer is not None
            and peer in subscribed
        ):
            return events
        events.append(_message_event("message_new", message, chats=chats))
        return events
    if isinstance(update, (UpdateEditMessage, UpdateEditChannelMessage)):
        message = update.message
        peer = (
            _peer_id(message.peer_id)
            if getattr(message, "peer_id", None) is not None
            else None
        )
        if (
            isinstance(update, UpdateEditChannelMessage)
            and peer is not None
            and peer in subscribed
        ):
            return events
        events.append(_message_event("message_edit", message, chats=chats))
        return events
    if isinstance(update, UpdateDeleteChannelMessages):
        peer = _peer_id(types.PeerChannel(update.channel_id))
        if peer in subscribed:
            return events
        events.append(
            {"type": "message_delete", "peer": peer, "ids": list(update.messages)}
        )
        return events
    if isinstance(update, UpdateDeleteMessages):
        if private_deletes:
            events.append(
                {
                    "type": "message_delete",
                    "peer": None,
                    "ids": list(update.messages),
                }
            )
            return events
        _bump_skipped(skipped, type(update).__name__, len(update.messages) or 1)
        return events
    if isinstance(update, UpdateChannelTooLong):
        peer = _peer_id(types.PeerChannel(update.channel_id))
        if peer in subscribed:
            return events
        events.append({"type": "channel_activity", "peer": peer})
        return events
    _bump_skipped(skipped, type(update).__name__)
    return events


def _map_messages(messages, *, kind: str, chats, skip_peers: set[int]) -> list[dict]:
    out: list[dict] = []
    for message in messages or ():
        peer = (
            _peer_id(message.peer_id)
            if getattr(message, "peer_id", None) is not None
            else None
        )
        if peer is not None and peer in skip_peers:
            continue
        out.append(_message_event(kind, message, chats=chats))
    return out


def _gap(scope, *, reason: str) -> dict:
    recover: dict[str, Any] = {"edits_deletes": "lost"}
    if scope == "common":
        recover["creation"] = (
            "re-read chats of interest with: tg read CHAT --after-id N"
        )
    else:
        recover["creation"] = f"re-read with: tg read {scope} --after-id N"
    return {"scope": scope, "reason": reason, "recover": recover}


async def _poll_common(
    tg, cursor: ChangesCursor, *, skipped: dict, private_deletes: bool = False
) -> tuple[list[dict], ChangesCursor, dict | None, list]:
    events: list[dict] = []
    gap = None
    requests: list = []
    subscribed = set(cursor.channels)
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
            gap = _gap("common", reason="differenceTooLong")
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
            chats = list(result.chats or ())
            events.extend(
                _map_messages(
                    result.new_messages,
                    kind="message_new",
                    chats=chats,
                    skip_peers=subscribed,
                )
            )
            for update in result.other_updates or ():
                events.extend(
                    _map_update(
                        update,
                        subscribed=subscribed,
                        chats=chats,
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
    new_cursor = changes_cursor.replace_common(
        cursor, pts=pts, qts=qts, date=date, seq=seq
    )
    return events, new_cursor, gap, requests


async def _poll_channel(
    tg, cursor: ChangesCursor, peer: int, *, skipped: dict
) -> tuple[list[dict], ChangesCursor, dict | None, list]:
    events: list[dict] = []
    gap = None
    requests: list = []
    pts = cursor.channels[peer]
    entity = await tg.get_entity(peer)
    input_channel = await tg.get_input_entity(entity)
    while True:
        request = GetChannelDifferenceRequest(
            channel=input_channel,
            filter=ChannelMessagesFilterEmpty(),
            pts=pts,
            limit=CHANNEL_DIFF_LIMIT,
        )
        requests.append(request)
        result = await tg(request)
        if isinstance(result, ChannelDifferenceEmpty):
            pts = int(result.pts)
            break
        if isinstance(result, ChannelDifferenceTooLong):
            gap = _gap(peer, reason="channelDifferenceTooLong")
            dialog_pts = getattr(result.dialog, "pts", None)
            if isinstance(dialog_pts, int):
                pts = dialog_pts
            else:
                pts = await _channel_pts(tg, entity)
            break
        if isinstance(result, ChannelDifference):
            chats = list(result.chats or ())
            events.extend(
                _map_messages(
                    result.new_messages,
                    kind="message_new",
                    chats=chats,
                    skip_peers=set(),
                )
            )
            for update in result.other_updates or ():
                events.extend(
                    _map_update(update, subscribed=set(), chats=chats, skipped=skipped)
                )
            pts = int(result.pts)
            if getattr(result, "final", None):
                break
            continue
        _bump_skipped(skipped, type(result).__name__)
        break
    return events, changes_cursor.with_channel(cursor, peer, pts), gap, requests


async def once(
    tg, cursor: ChangesCursor, *, private_deletes: bool = False
) -> tuple[dict, ChangesCursor, list]:
    """One difference pass. Returns document body, cursor, requests made.

    When ``private_deletes`` is true, ``UpdateDeleteMessages`` becomes
    ``message_delete`` events with ``peer: null`` (archive resolves peers
    from the local store). Public ``tg changes`` keeps the default skip.
    """
    skipped: dict[str, int] = {}
    events: list[dict] = []
    gap = None
    all_requests: list = []

    common_events, cursor, common_gap, reqs = await _poll_common(
        tg, cursor, skipped=skipped, private_deletes=private_deletes
    )
    events.extend(common_events)
    all_requests.extend(reqs)
    if common_gap is not None:
        gap = common_gap

    for peer in sorted(cursor.channels):
        ch_events, cursor, ch_gap, reqs = await _poll_channel(
            tg, cursor, peer, skipped=skipped
        )
        events.extend(ch_events)
        all_requests.extend(reqs)
        if ch_gap is not None and gap is None:
            gap = ch_gap

    return {"events": events, "gap": gap, "skipped": skipped}, cursor, all_requests


def _document(
    doc: dict, cursor: ChangesCursor, *, binding_key: bytes | None = None
) -> dict:
    return {
        "events": doc["events"],
        "next_cursor": changes_cursor.encode(cursor, binding_key=binding_key),
        "gap": doc["gap"],
        "skipped": doc["skipped"],
    }


async def init_changes(
    tg, peers: list[str] | None = None, *, binding_key: bytes | None = None
) -> dict:
    state = await tg(GetStateRequest())
    cursor = _from_state(state)
    for ref in peers or ():
        entity = await _resolve_channel(tg, ref)
        peer = _peer_id(entity)
        pts = await _channel_pts(tg, entity)
        cursor = changes_cursor.with_channel(cursor, peer, pts)
        note(f"subscribed {peer} at pts {pts} (no history replay)")
    return _document(
        {"events": [], "gap": None, "skipped": {}},
        cursor,
        binding_key=binding_key,
    )


async def run_changes(
    tg,
    *,
    cursor_text: str | None,
    binding_key: bytes,
    init: bool = False,
    peers: list[str] | None = None,
    drop_peers: list[str] | None = None,
    wait: float | None = None,
) -> dict:
    peers = list(peers or ())
    drop_peers = list(drop_peers or ())
    if init:
        if cursor_text is not None:
            raise PolicyError("changes --init rejects --cursor; start a new baseline")
        if drop_peers:
            raise PolicyError("changes --init rejects --drop-peer")
        if wait is not None:
            raise PolicyError("changes --init rejects --wait")
        return await init_changes(tg, peers, binding_key=binding_key)

    if cursor_text is None:
        raise PolicyError("changes cursor is required; run: tg changes --init")
    if wait is not None and wait <= 0:
        raise PolicyError("--wait must be a positive number of seconds")

    cursor = changes_cursor.decode(
        cursor_text, binding_key=binding_key, require_bound=True
    )

    for ref in drop_peers:
        entity = await _resolve_channel(tg, ref)
        cursor = changes_cursor.without_channel(cursor, _peer_id(entity))

    for ref in peers:
        entity = await _resolve_channel(tg, ref)
        peer = _peer_id(entity)
        if peer in cursor.channels:
            continue
        pts = await _channel_pts(tg, entity)
        cursor = changes_cursor.with_channel(cursor, peer, pts)
        note(f"subscribed {peer} at pts {pts} (no history replay)")

    doc, cursor, _ = await once(tg, cursor)
    if wait is None:
        return _document(doc, cursor, binding_key=binding_key)

    start = _now()
    deadline = start + wait

    if not doc["events"] and doc["gap"] is None:
        while _now() < deadline and not doc["events"] and doc["gap"] is None:
            remaining = deadline - _now()
            if remaining <= 0:
                break
            await _async_sleep(min(POLL_SLEEP_S, remaining))
            if _now() >= deadline:
                break
            doc, cursor, _ = await once(tg, cursor)

    if doc["events"] and _now() < deadline:
        settle_end = min(_now() + SETTLE_SECONDS, deadline)
        while _now() < settle_end:
            remaining = settle_end - _now()
            if remaining <= 0:
                break
            await _async_sleep(min(POLL_SLEEP_S, remaining))
            if _now() >= settle_end:
                break
            more, cursor, _ = await once(tg, cursor)
            doc["events"].extend(more["events"])
            if more["gap"] is not None and doc["gap"] is None:
                doc["gap"] = more["gap"]
            for key, value in (more["skipped"] or {}).items():
                doc["skipped"][key] = doc["skipped"].get(key, 0) + value

    return _document(doc, cursor, binding_key=binding_key)
