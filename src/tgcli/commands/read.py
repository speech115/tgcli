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


async def fetch_messages(tg, chat: str, limit: int = 20) -> dict:
    try:
        entity = await tg.get_entity(chat)
    except ValueError:
        raise NotFoundError(f"dialog not found: {chat!r}") from None

    messages = []
    async for message in tg.iter_messages(entity, limit=limit):
        messages.append(
            {
                "id": message.id,
                "date": message.date.isoformat() if message.date else None,
                "from": {"id": message.sender_id, "name": _sender_name(message)},
                "text": message.text or "",
                "media": type(message.media).__name__ if message.media else None,
                "reply_to": message.reply_to_msg_id,
            }
        )

    return {
        "dialog": {"id": entity.id, "name": _dialog_name(entity, chat)},
        "messages": messages,
    }


def to_rows(data: dict) -> list[tuple]:
    return [
        (message["id"], message["date"], message["from"]["name"], message["text"].replace("\n", " "))
        for message in data["messages"]
    ]
