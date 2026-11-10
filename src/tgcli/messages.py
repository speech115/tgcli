"""How a Telegram message and its dialog look in tgcli's JSON (no network).

`tg read`, search, export, `tg run`'s `msg`, and the local archive's stored
payload all use this one projection, so a field change here is a change to
each of them (and to what archive revision detection compares).
"""

from telethon import utils
from telethon.helpers import add_surrogate
from telethon.tl import types
from telethon.tl.types import MessageEntityCustomEmoji


def _sender_name(message) -> str | None:
    sender = getattr(message, "sender", None)
    if sender is None:
        return None
    parts = [getattr(sender, "first_name", None), getattr(sender, "last_name", None)]
    name = " ".join(part for part in parts if part)
    return name or getattr(sender, "title", None) or getattr(sender, "username", None)


def dialog_name(entity, fallback: str) -> str:
    return (
        getattr(entity, "title", None)
        or getattr(entity, "first_name", None)
        or getattr(entity, "username", None)
        or fallback
    )


def _permalink(entity, message_id: int) -> str | None:
    if entity is None:
        return None
    username = getattr(entity, "username", None)
    if username:
        return f"https://t.me/{username}/{message_id}"
    if getattr(entity, "broadcast", False) or getattr(entity, "megagroup", False):
        return f"https://t.me/c/{entity.id}/{message_id}"
    return None


def media_kind(message) -> str | None:
    """Return the stable media category used by archive acquisition."""
    if not getattr(message, "media", None):
        return None
    if getattr(message, "photo", None):
        return "photo"
    if getattr(message, "video_note", None):
        return "video_note"
    if getattr(message, "video", None):
        return "video"
    if getattr(message, "voice", None):
        return "voice"
    if getattr(message, "audio", None):
        return "audio"
    if getattr(message, "document", None):
        return "document"
    return None


# Every value media_kind returns.
MEDIA_KINDS = ("photo", "video", "video_note", "audio", "voice", "document")


def _media_info(message) -> dict | None:
    file = getattr(message, "file", None)
    if file is None:
        return None
    return {
        "name": getattr(file, "name", None),
        "mime": getattr(file, "mime_type", None),
        "size": getattr(file, "size", None),
        "duration": getattr(file, "duration", None),
        "width": getattr(file, "width", None),
        "height": getattr(file, "height", None),
    }


def _voice_played(message) -> bool | None:
    """Expose Telegram's voice-message listened flag without false positives."""
    if getattr(message, "voice", None) is None:
        return None
    media_unread = getattr(message, "media_unread", None)
    return None if media_unread is None else not media_unread


def _reactions(message) -> list[dict]:
    results = getattr(getattr(message, "reactions", None), "results", None) or []
    output = []
    for item in results:
        reaction = getattr(item, "reaction", None)
        emoji = getattr(reaction, "emoticon", None)
        custom = getattr(reaction, "document_id", None)
        output.append(
            {"emoji": emoji or (str(custom) if custom else None), "count": item.count}
        )
    return output


def _custom_emoji(message) -> list[dict]:
    # Entities index into the raw text (.message), not the formatted .text.
    return custom_emoji(
        getattr(message, "message", None) or "",
        getattr(message, "entities", None),
    )


def custom_emoji(raw: str, entities) -> list[dict]:
    """Custom (premium) emoji in a body, so their ids can be reused.

    Each item is ``{id, emoji, offset, length}`` where ``id`` is the reusable
    ``document_id`` (the ``emoji-id`` for ``--format html``) and ``emoji`` is the
    fallback unicode glyph it covers. Offsets/lengths are UTF-16 code units, so
    the covered glyph is sliced via Telethon's surrogate helpers.
    """
    if not entities:
        return []
    surrogate = add_surrogate(raw)
    output = []
    for entity in entities:
        if not isinstance(entity, MessageEntityCustomEmoji):
            continue
        glyph = utf16_slice(surrogate, entity.offset, entity.length)
        output.append(
            {
                "id": str(entity.document_id),
                "emoji": glyph,
                "offset": entity.offset,
                "length": entity.length,
            }
        )
    return output


def utf16_slice(surrogate: str, offset: int, length: int) -> str:
    """The text an entity covers, given Telethon's surrogate form of the body.

    An entity whose offset or length falls inside a UTF-16 pair would leave a
    lone surrogate, which ``del_surrogate`` refuses to decode and stdout could
    not encode anyway; that half becomes U+FFFD so the rest of the page prints.
    """
    piece = surrogate[offset : offset + length]
    return piece.encode("utf-16", "surrogatepass").decode("utf-16", "replace")


def _forwarded_from(message) -> dict | None:
    forward = getattr(message, "forward", None)
    if forward is None:
        return None
    date = getattr(forward, "date", None)
    return {
        "name": getattr(forward, "from_name", None),
        "id": getattr(forward, "sender_id", None) or getattr(forward, "chat_id", None),
        "date": date.isoformat() if date else None,
    }


def _topic_id(message) -> int | None:
    reply = getattr(message, "reply_to", None)
    if reply is None or not getattr(reply, "forum_topic", False):
        return None
    return getattr(reply, "reply_to_top_id", None) or getattr(
        reply, "reply_to_msg_id", None
    )


def _reply_peer_matches_chat(peer, entity) -> bool:
    """True when reply_to_peer_id names the chat being read (or is absent)."""
    if peer is None or entity is None:
        return True
    eid = getattr(entity, "id", None)
    if not isinstance(eid, int):
        return True
    if utils.get_peer_id(peer) == eid:
        return True
    if isinstance(peer, types.PeerChannel):
        return peer.channel_id == eid
    if isinstance(peer, types.PeerUser):
        return peer.user_id == eid
    if isinstance(peer, types.PeerChat):
        return peer.chat_id == eid
    return False


def _reply_to(message, entity=None) -> int | dict | None:
    msg_id = message.reply_to_msg_id
    if msg_id is None:
        return None
    header = getattr(message, "reply_to", None)
    peer = getattr(header, "reply_to_peer_id", None) if header is not None else None
    if _reply_peer_matches_chat(peer, entity):
        return msg_id
    return {"id": msg_id, "peer": utils.get_peer_id(peer)}


def _quote_text(message) -> str | None:
    header = getattr(message, "reply_to", None)
    text = getattr(header, "quote_text", None) if header is not None else None
    return text if isinstance(text, str) else None


def body_text(message) -> str:
    """Prefer Telethon's formatted ``.text``; fall back to raw ``.message``.

    ``updates.getChannelDifference`` yields patched ``Message`` objects whose
    ``.text`` property is ``None`` even when the TL ``message`` field holds the
    body — live acceptance of ``tg changes`` hit this (ADR-0063).
    """
    text = getattr(message, "text", None)
    if text is not None:
        return text or ""
    return getattr(message, "message", None) or ""


def message_to_dict(message, entity=None) -> dict:
    edit_date = getattr(message, "edit_date", None)
    return {
        "id": message.id,
        "date": message.date.isoformat() if message.date else None,
        "from": {
            "id": message.sender_id,
            "name": _sender_name(message),
            "username": getattr(getattr(message, "sender", None), "username", None),
        },
        "text": body_text(message),
        "media": type(message.media).__name__ if message.media else None,
        "media_info": _media_info(message),
        "media_kind": media_kind(message),
        "voice_played": _voice_played(message),
        "reply_to": _reply_to(message, entity),
        "quote_text": _quote_text(message),
        "permalink": _permalink(entity, message.id),
        "edited_at": edit_date.isoformat() if edit_date else None,
        "outgoing": bool(getattr(message, "out", False)),
        "forwarded_from": _forwarded_from(message),
        "reactions": _reactions(message),
        "custom_emoji": _custom_emoji(message),
        "topic_id": _topic_id(message),
        "grouped_id": getattr(message, "grouped_id", None),
        "is_service": getattr(message, "action", None) is not None,
    }


def sanitize_plain_text(value: str | None) -> str | None:
    if value is None:
        return None
    return "".join(char if char.isprintable() else " " for char in value)


def message_rows(data: dict) -> list[tuple]:
    """Plain rows for a `{messages: [...]}` page or a single `{message: ...}`."""
    messages = data["messages"] if "messages" in data else [data["message"]]
    return [
        (
            message["id"],
            message["date"],
            sanitize_plain_text(message["from"]["name"]),
            sanitize_plain_text(message["text"]),
        )
        for message in messages
    ]
