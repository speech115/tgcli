"""Source-kind and author attribution rules for channel clones (ADR-0021)."""

from copy import copy
from dataclasses import dataclass

from telethon import errors as telethon_errors, utils
from telethon.tl import types

from tgcli.errors import PolicyError


@dataclass(frozen=True)
class Author:
    """Author label; mention_user_id set → render it as a profile mention.

    ``lead`` (ADR-0050) replaces the speaker-label ``{text}: `` shape with a
    forward lead-in such as ``Переслано от ``; empty ``lead`` keeps the
    historical speaker form byte for byte.
    """

    text: str
    mention_user_id: int | None = None
    lead: str = ""


def source_kind(entity) -> str:
    if isinstance(entity, types.User):
        return "dialog"
    if isinstance(entity, types.Chat):
        if (target := getattr(entity, "migrated_to", None)) is not None:
            target_id = getattr(target, "channel_id", None)
            hint = f"channel {target_id}" if target_id is not None else "the supergroup"
            raise PolicyError(
                f"clone source basic group migrated to a supergroup; "
                f"clone {hint} instead"
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


def destination_title(title: str) -> str:
    """Visible tool marking on tool-created peers (ADR-0044)."""
    return f"[Clone] {title}"


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
        entity = await _resolve(tg, peer, cache, cooldown)
    return _identify(entity, getattr(message, "sender_id", None))


async def _resolve(tg, peer, cache: dict, cooldown):
    """The entity behind a peer, or None when Telegram refuses to name it.

    A post can name a peer this account cannot resolve — a private channel
    (live-proven: `[икона]` 54 forwards from one), a deleted account. Every
    refusal is a missing label, not a failed sync; only a FloodWait still
    stops the run (ADR-0045).
    """
    key = peer_key(peer)
    if key not in cache:
        try:
            cache[key] = await cooldown(lambda: tg.get_entity(peer))
        except telethon_errors.FloodWaitError:
            raise
        except (ValueError, telethon_errors.RPCError):
            cache[key] = None
    return cache[key]


FORWARD_LEAD = "Переслано от "


def _with_forward_lead(author: Author) -> Author:
    return Author(
        text=author.text, mention_user_id=author.mention_user_id, lead=FORWARD_LEAD
    )


async def forwarded_author_of(tg, message, cache: dict, cooldown) -> Author:
    """Label for a posts-leg reupload/snapshot whose source post is itself a forward.

    Sibling of ``author_of``: that answers who sent the message; this answers
    who ``fwd_from`` attributes it to (ADR-0050). Never invents a discussion
    origin — ``saved_from_peer`` is unused.
    """
    fwd = getattr(message, "fwd_from", None)
    if fwd is None:
        return Author(text="", lead="Переслано")
    peer = getattr(fwd, "from_id", None)
    if peer is not None:
        entity = await _resolve(tg, peer, cache, cooldown)
        if entity is not None:
            return _with_forward_lead(_identify(entity, None))
    from_name = getattr(fwd, "from_name", None)
    if isinstance(from_name, str) and from_name:
        return Author(text=from_name, lead=FORWARD_LEAD)
    post_author = getattr(fwd, "post_author", None)
    if isinstance(post_author, str) and post_author:
        return Author(text=post_author, lead=FORWARD_LEAD)
    return Author(text="", lead="Переслано")


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
    if author.lead:
        prefix = f"{author.lead}{author.text}\n\n"
        mention_offset = utf16_len(author.lead)
    else:
        prefix = f"{author.text}: \n\n"
        mention_offset = 0
    mention = ()
    if author.mention_user_id is not None:
        mention = (
            types.MessageEntityMentionName(
                offset=mention_offset,
                length=utf16_len(author.text),
                user_id=author.mention_user_id,
            ),
        )
    return with_prefix(text, entities, prefix, mention)
