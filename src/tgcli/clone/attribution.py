"""Source-kind and author attribution rules for channel clones (ADR-0021)."""

from copy import copy
from dataclasses import dataclass

from telethon import utils
from telethon.tl import types

from tgcli.errors import PolicyError


@dataclass(frozen=True)
class Author:
    """Author label; mention_user_id set → render it as a profile mention."""

    text: str
    mention_user_id: int | None = None


def source_kind(entity) -> str:
    if isinstance(entity, types.User):
        return "dialog"
    if isinstance(entity, types.Chat):
        if (target := getattr(entity, "migrated_to", None)) is not None:
            target_id = getattr(target, "channel_id", None)
            hint = f"channel {target_id}" if target_id is not None else "the supergroup"
            raise PolicyError(
                f"clone source basic group migrated to a supergroup; clone {hint} instead"
            )
        if getattr(entity, "deactivated", False):
            raise PolicyError("clone source basic group is deactivated")
        return "basic"
    if getattr(entity, "megagroup", False):
        return "forum" if getattr(entity, "forum", False) else "megagroup"
    if getattr(entity, "broadcast", False):
        return "broadcast"
    raise PolicyError("clone source type is not supported")


def display_name(entity) -> str:
    return (
        getattr(entity, "title", None)
        or utils.get_display_name(entity)
        or f"id {entity.id}"
    )


def same_peer(peer, source) -> bool:
    if isinstance(source, types.User):
        return isinstance(peer, types.PeerUser) and peer.user_id == source.id
    if isinstance(source, types.Chat):
        return isinstance(peer, types.PeerChat) and peer.chat_id == source.id
    return isinstance(peer, types.PeerChannel) and peer.channel_id == source.id


def peer_key(peer) -> tuple[str, int | None]:
    """Stable peer kind and raw id for in-process caches and loss reports."""
    if isinstance(peer, types.PeerChannel):
        return "channel", peer.channel_id
    if isinstance(peer, types.PeerUser):
        return "user", peer.user_id
    if isinstance(peer, types.PeerChat):
        return "chat", peer.chat_id
    return "other", None


def _active_username(entity) -> str | None:
    if username := getattr(entity, "username", None):
        return username
    for item in getattr(entity, "usernames", None) or ():
        if getattr(item, "active", False) and getattr(item, "username", None):
            return item.username
    return None


def _identify(entity, sender_id) -> Author:
    if entity is None:
        return Author(text=f"id {sender_id or 'unknown'}")
    name = display_name(entity)
    if (username := _active_username(entity)) is not None:
        return Author(text=f"{name} (@{username})")
    if isinstance(entity, types.User):
        return Author(text=name, mention_user_id=entity.id)
    return Author(text=name)


async def author_of(tg, source, message, me, cache: dict, cooldown) -> Author:
    peer = getattr(message, "from_id", None)
    if (
        isinstance(peer, types.PeerUser)
        and peer.user_id == me.id
        or peer is None
        and getattr(message, "out", False)
    ):
        entity = me
    elif peer is None and isinstance(source, types.User):
        entity = source
    elif peer is None:
        signature = getattr(message, "post_author", None)
        return (
            Author(text=signature)
            if isinstance(signature, str) and signature
            else _identify(None, getattr(message, "sender_id", None))
        )
    else:
        key = peer_key(peer)
        if key not in cache:
            try:
                cache[key] = await cooldown(tg.get_entity(peer))
            except ValueError:
                cache[key] = None
        entity = cache[key]
    return _identify(entity, getattr(message, "sender_id", None))


def utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def with_prefix(
    text: str, entities, prefix: str, prefix_entities=()
) -> tuple[str, list | None]:
    """Prepend ``prefix`` and shift body entity offsets by its UTF-16 length."""
    original = list(entities or ())
    if not prefix:
        return text, original or None
    shift = utf16_len(prefix)
    result = [copy(entity) for entity in prefix_entities]
    for entity in original:
        shifted_entity = copy(entity)
        shifted_entity.offset += shift
        result.append(shifted_entity)
    return prefix + text, result or None


def prefixed(text: str, entities, author: Author | None) -> tuple[str, list | None]:
    if author is None:
        return with_prefix(text, entities, "", ())
    prefix = f"{author.text}: \n\n"
    mention = ()
    if author.mention_user_id is not None:
        mention = (
            types.MessageEntityMentionName(
                offset=0,
                length=utf16_len(author.text),
                user_id=author.mention_user_id,
            ),
        )
    return with_prefix(text, entities, prefix, mention)
