from tgcli import chatref
from tgcli.errors import NotFoundError
from tgcli.messages import dialog_name, message_to_dict


async def fetch_message(tg, chat: str, message_id: int, context: int = 0) -> dict:
    try:
        entity = await tg.get_entity(chatref.parse(chat))
    except ValueError:
        raise NotFoundError(f"dialog not found: {chat!r}") from None

    message = await tg.get_messages(entity, ids=message_id)
    if message is None:
        raise NotFoundError(f"message not found: {message_id}")

    data = {
        "dialog": {"id": entity.id, "name": dialog_name(entity, chat)},
        "message": message_to_dict(message, entity),
    }
    if context > 0:
        ids = [
            item
            for item in range(message_id - context, message_id + context + 1)
            if item > 0 and item != message_id
        ]
        neighbors = await tg.get_messages(entity, ids=ids)
        data["context"] = [
            message_to_dict(item, entity) for item in neighbors if item is not None
        ]
    return data


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
        "dialog": {"id": entity.id, "name": dialog_name(entity, chat)},
        "messages": messages,
        "page": {
            "oldest_id": min(ids) if ids else None,
            "newest_id": max(ids) if ids else None,
        },
    }
