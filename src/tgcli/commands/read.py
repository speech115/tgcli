from tgcli import chatref
from tgcli.errors import NotFoundError


def _sender_name(message) -> str | None:
    sender = getattr(message, "sender", None)
    if sender is None:
        return None
    parts = [getattr(sender, "first_name", None), getattr(sender, "last_name", None)]
    name = " ".join(part for part in parts if part)
    return name or getattr(sender, "title", None) or getattr(sender, "username", None)


def _dialog_name(entity, fallback: str) -> str:
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
        "text": message.text or "",
        "media": type(message.media).__name__ if message.media else None,
        "media_info": _media_info(message),
        "reply_to": message.reply_to_msg_id,
        "permalink": _permalink(entity, message.id),
        "edited_at": edit_date.isoformat() if edit_date else None,
        "outgoing": bool(getattr(message, "out", False)),
        "forwarded_from": _forwarded_from(message),
        "reactions": _reactions(message),
        "topic_id": _topic_id(message),
        "grouped_id": getattr(message, "grouped_id", None),
        "is_service": getattr(message, "action", None) is not None,
    }


async def fetch_message(tg, chat: str, message_id: int) -> dict:
    try:
        entity = await tg.get_entity(chatref.parse(chat))
    except ValueError:
        raise NotFoundError(f"dialog not found: {chat!r}") from None

    message = await tg.get_messages(entity, ids=message_id)
    if message is None:
        raise NotFoundError(f"message not found: {message_id}")

    return {
        "dialog": {"id": entity.id, "name": _dialog_name(entity, chat)},
        "message": message_to_dict(message, entity),
    }


async def fetch_messages(
    tg,
    chat: str,
    limit: int = 20,
    *,
    before_id: int | None = None,
    after_id: int | None = None,
    since=None,
    until=None,
    topic: int | None = None,
) -> dict:
    try:
        entity = await tg.get_entity(chatref.parse(chat))
    except ValueError:
        raise NotFoundError(f"dialog not found: {chat!r}") from None

    messages = []
    async for message in tg.iter_messages(
        entity,
        limit=limit,
        offset_id=before_id or 0,
        min_id=after_id or 0,
        offset_date=until,
        reply_to=topic,
    ):
        if since is not None and message.date is not None and message.date < since:
            break
        messages.append(message_to_dict(message, entity))

    ids = [message["id"] for message in messages]
    return {
        "dialog": {"id": entity.id, "name": _dialog_name(entity, chat)},
        "messages": messages,
        "page": {
            "oldest_id": min(ids) if ids else None,
            "newest_id": max(ids) if ids else None,
        },
    }


def to_rows(data: dict) -> list[tuple]:
    return [
        (
            message["id"],
            message["date"],
            sanitize_plain_text(message["from"]["name"]),
            sanitize_plain_text(message["text"]),
        )
        for message in data["messages"]
    ]


def sanitize_plain_text(value: str | None) -> str | None:
    if value is None:
        return None
    return "".join(char if char.isprintable() else " " for char in value)
