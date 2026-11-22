"""Resolving a clone's source and destination peers on Telegram.

`clone init` and `clone sync` both open the same two peers and turn the same
refusals into the same operable errors, so the resolution lives here, beside
the clone engine, instead of in the command layer.
"""

from __future__ import annotations

from telethon.tl import types

from tgcli import chatref
from tgcli.clone import attribution, discussion, lookup
from tgcli.errors import NotFoundError, PolicyError

DESTINATION_UNAVAILABLE = discussion.PEER_UNAVAILABLE


async def resolve_destination(tg, destination_peer_id: int):
    try:
        return await tg.get_entity(types.PeerChannel(destination_peer_id))
    except DESTINATION_UNAVAILABLE:
        raise PolicyError("clone destination is unavailable") from None


def record_destination_name(clone_state, destination) -> None:
    """Remember what the destination is called, for the offline status listing.

    A clone destination is private and unnamed to everyone but its creator, so
    `clone status` could only ever print a bare peer id (#175). Every command
    that resolves the peer refreshes the recorded name; the caller saves.
    """
    clone_state.destination_title = getattr(destination, "title", None)
    clone_state.destination_username = getattr(destination, "username", None)


async def resolve_source(tg, source: str, *, account_user_id: int | None = None):
    """Resolve SOURCE to an entity. Pass ``account_user_id`` where a clone must
    already exist, so a title can be answered from state instead of Telegram."""
    ref = (
        lookup.recorded_source_ref(account_user_id, source)
        if account_user_id is not None and not source.lstrip("-").isdigit()
        else None
    )
    try:
        entity = await chatref.resolve(tg, ref or source)
    except DESTINATION_UNAVAILABLE:
        raise NotFoundError(f"clone source not found: {source!r}") from None
    kind = attribution.source_kind(entity)
    return entity, kind, attribution.display_name(entity)
