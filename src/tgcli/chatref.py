"""Normalize CLI chat references and verify resolved peer identity.

Telethon treats a bare digit string as a phone number, so the documented
"dialog id" form must become an int before get_entity(). Its memory cache is
keyed by bare ids, and display names can match several session rows, so reads
verify negative marked ids and preview commits verify the peer they showed.
"""

from typing import Any

from telethon import utils
from telethon.tl import types

from tgcli.errors import PolicyError


def parse(chat: str) -> int | str:
    if chat.lstrip("-").isdigit():
        return int(chat)
    return chat


def marked_peer_id(entity) -> int | None:
    try:
        return utils.get_peer_id(entity)
    except TypeError:
        return None


async def _input_peer_id(tg, peer) -> int | None:
    if isinstance(peer, types.InputPeerSelf):
        me = await tg.get_me()
        return None if me is None else marked_peer_id(me)
    return marked_peer_id(peer)


async def resolve(tg, chat):
    """Resolve a target without accepting a different marked peer.

    Telethon's memory cache is keyed by bare ids, so even a negative marked
    id can resolve to a user or another chat type with the same bare id.
    Validate the full peer identity before a caller reads or returns data.
    Positive bare ids keep the CLI's existing, type-ambiguous resolution.
    """
    ref = parse(chat) if isinstance(chat, str) else chat
    entity = await tg.get_entity(ref)
    if isinstance(ref, int) and ref < 0:
        actual = marked_peer_id(entity)
        if actual is not None and actual != ref:
            raise ValueError("resolved peer does not match the marked dialog id")
    return entity


async def resolve_input(
    tg,
    chat,
    *,
    expected_peer_id: int | None = None,
) -> Any:
    """Resolve an input peer, checking negative marked ids and preview pinning."""
    ref = parse(chat) if isinstance(chat, str) else chat
    peer = await tg.get_input_entity(ref)
    if isinstance(peer, types.TLObject):
        actual = await _input_peer_id(tg, peer)
        if isinstance(ref, int) and ref < 0 and actual != ref:
            raise ValueError("resolved peer does not match the marked dialog id")
        if expected_peer_id is not None and actual != expected_peer_id:
            raise PolicyError(
                "target peer changed since its preview; make a new preview"
            )
    return peer
