"""Source-kind and author attribution rules for channel clones (ADR-0021)."""

from copy import copy

from telethon import utils
from telethon.tl import types

from tgcli.errors import PolicyError


def source_kind(entity) -> str:
    if isinstance(entity, types.User):
        return "dialog"
    if isinstance(entity, types.Chat):
        if (target := getattr(entity, "migrated_to", None)) is not None:
            raise PolicyError("clone source basic group migrated to a supergroup; "
                              f"clone channel {target.channel_id} instead")
        if getattr(entity, "deactivated", False):
            raise PolicyError("clone source basic group is deactivated")
        return "basic"
    if getattr(entity, "megagroup", False):
        if getattr(entity, "forum", False):
            raise PolicyError("clone source forum topics are not supported")
        return "megagroup"
    if getattr(entity, "broadcast", False):
        return "broadcast"
    raise PolicyError("clone source type is not supported")


def display_name(entity) -> str:
    return getattr(entity, "title", None) or utils.get_display_name(entity) or f"id {entity.id}"


def same_peer(peer, source) -> bool:
    if isinstance(source, types.User):
        return isinstance(peer, types.PeerUser) and peer.user_id == source.id
    if isinstance(source, types.Chat):
        return isinstance(peer, types.PeerChat) and peer.chat_id == source.id
    return isinstance(peer, types.PeerChannel) and peer.channel_id == source.id


def _peer_key(peer) -> tuple[str, int | None]:
    return type(peer).__name__, utils.get_peer_id(peer) if peer is not None else None


async def author_name(tg, source, message, me, cache: dict, cooldown) -> str:
    peer = getattr(message, "from_id", None)
    if (isinstance(peer, types.PeerUser) and peer.user_id == me.id
            or peer is None and getattr(message, "out", False)):
        entity = me
    elif peer is None and isinstance(source, types.User):
        entity = source
    elif peer is None:
        sender_id = getattr(message, "sender_id", None)
        return getattr(message, "post_author", None) or f"id {sender_id or 'unknown'}"
    else:
        key = _peer_key(peer)
        if key not in cache:
            try:
                cache[key] = await cooldown(tg.get_entity(peer))
            except ValueError:
                cache[key] = None
        entity = cache[key]
    sender_id = getattr(message, "sender_id", None)
    return display_name(entity) if entity is not None else f"id {sender_id or 'unknown'}"


def prefixed(text: str, entities, author: str | None) -> tuple[str, list | None]:
    original = list(entities or ())
    if author is None:
        return text, original or None
    prefix = f"{author}: "
    shift = len(prefix.encode("utf-16-le")) // 2
    shifted = []
    for entity in original:
        shifted_entity = copy(entity)
        shifted_entity.offset += shift
        shifted.append(shifted_entity)
    return prefix + text, shifted or None
