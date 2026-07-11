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


def message_to_dict(message) -> dict:
    return {
        "id": message.id,
        "date": message.date.isoformat() if message.date else None,
        "from": {"id": message.sender_id, "name": _sender_name(message)},
        "text": message.text or "",
        "media": type(message.media).__name__ if message.media else None,
        "reply_to": message.reply_to_msg_id,
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
        "message": message_to_dict(message),
    }


async def fetch_messages(tg, chat: str, limit: int = 20) -> dict:
    try:
        entity = await tg.get_entity(chatref.parse(chat))
    except ValueError:
        raise NotFoundError(f"dialog not found: {chat!r}") from None

    messages = []
    async for message in tg.iter_messages(entity, limit=limit):
        messages.append(message_to_dict(message))

    return {
        "dialog": {"id": entity.id, "name": _dialog_name(entity, chat)},
        "messages": messages,
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
