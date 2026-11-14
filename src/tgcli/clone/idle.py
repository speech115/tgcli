"""Finishing a `clone sync` that has nothing to copy (ADR-0126).

A scheduled run finds nothing new almost every time, and the full run opens
the destination, walks its tail and lists the roster to learn that. Telegram
numbers a chat's messages in one rising sequence, so a source whose newest
message is not past the clone's cursor has nothing new, and the same holds for
the discussion group and `discussion_cursor`. Two one-message history reads
answer it, on peers the session already holds.

Everything the full run does besides copying is named here, so none of it
stops silently:

- the destination checks (`resolve_destination`, the tail guards) stop a copy
  that would land on a damaged destination; with no copy there is nothing to
  guard, and the next run that copies makes them;
- the destination's recorded name refreshes on a run that copies;
- the pin carry-over, once resolved, answers from state; before that it reads
  the source on every completing run. Telegram writes a service message for
  a pin, which moves the newest id past the cursor, so that run is not idle;
- the roster is not tied to new messages, so it keeps its own trigger: a run
  is never idle while the roster is a day old (`roster.due`), and that run
  repeats the destination checks and the pin read as well;
- `clone init` owns the title, description, avatar and discussion relink.
"""

from __future__ import annotations

from datetime import UTC, datetime

from telethon.tl import functions, types

from tgcli import chatref
from tgcli.clone import discussion, lookup, roster, state
from tgcli.clone.state import CloneState, Comments

_PEER_CLASSES = {
    types.InputPeerChannel: ("channel", "channel_id"),
    types.InputPeerChat: ("chat", "chat_id"),
    types.InputPeerUser: ("user", "user_id"),
}


async def account_id(tg) -> int:
    """The account's user id: Telethon restores it from the session on
    connect, so a session that has run before asks Telegram nothing."""
    try:
        return (await tg.get_me(input_peer=True)).user_id
    except AttributeError:
        # Telethon knows the id but kept no access hash for it, and then
        # fails building the input peer; the full user is the way around.
        return (await tg.get_me()).id


async def source_peer(tg, source: str, account_user_id: int):
    """SOURCE as an input peer the session names without a full resolve, or
    None when it cannot (the caller then resolves the source in full)."""
    ref = chatref.parse(source)
    if isinstance(ref, str):
        ref = lookup.recorded_source_ref(account_user_id, source) or ref
    try:
        peer = await tg.get_input_entity(ref)
    except discussion.PEER_UNAVAILABLE:
        return None
    if isinstance(peer, types.InputPeerSelf):
        return types.InputPeerUser(user_id=account_user_id, access_hash=0)
    return peer if type(peer) in _PEER_CLASSES else None


def slot_id(peer, account_user_id: int) -> str:
    peer_class, field = _PEER_CLASSES[type(peer)]
    return state.peer_clone_id(account_user_id, peer_class, getattr(peer, field))


async def _newest_id(tg, peer) -> int | None:
    try:
        page = await tg(
            functions.messages.GetHistoryRequest(
                peer=peer,
                offset_id=0,
                offset_date=None,
                add_offset=0,
                limit=1,
                max_id=0,
                min_id=0,
                hash=0,
            )
        )
    except discussion.PEER_UNAVAILABLE:
        return None
    return max((item.id for item in page.messages), default=0)


def _runnable(clone_state: CloneState) -> bool:
    """Whether a full run would get past its own state checks."""
    return clone_state.destination_peer_id is not None and not (
        clone_state.comments == Comments.ENABLED and not clone_state.discussion_linked
    )


async def up_to_date(tg, clone_state: CloneState, peer) -> bool:
    """Whether neither the source nor its discussion group is past its cursor."""
    if not _runnable(clone_state) or roster.due(clone_state):
        return False
    newest = await _newest_id(tg, peer)
    if newest is None or newest > clone_state.cursor:
        return False
    if (
        clone_state.comments != Comments.ENABLED
        or clone_state.discussion_source_peer_id is None
    ):
        return True
    try:
        group = await tg.get_input_entity(
            types.PeerChannel(clone_state.discussion_source_peer_id)
        )
    except discussion.PEER_UNAVAILABLE:
        return False
    newest = await _newest_id(tg, group)
    return newest is not None and newest <= clone_state.discussion_cursor


def finish(clone_state: CloneState) -> None:
    clone_state.last_synced_at = datetime.now(UTC).isoformat()
    clone_state.retry_not_before = None
    state.save(clone_state)
