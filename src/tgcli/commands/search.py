from tgcli.commands.read import _dialog_name, message_to_dict
from tgcli.errors import NotFoundError


async def _entity(tg, chat: str):
    try:
        return await tg.get_entity(chat)
    except ValueError:
        raise NotFoundError(f"dialog not found: {chat!r}") from None


async def fetch_search(tg, chat: str, query: str, limit: int = 20) -> dict:
    entity = await _entity(tg, chat)
    messages = []
    async for message in tg.iter_messages(entity, search=query, limit=limit):
        messages.append(message_to_dict(message))
    return {
        "dialog": {"id": entity.id, "name": _dialog_name(entity, chat)},
        "query": query,
        "messages": messages,
    }


async def fetch_latest(tg, chat: str) -> dict:
    entity = await _entity(tg, chat)
    async for message in tg.iter_messages(entity, limit=1):
        return {
            "dialog": {"id": entity.id, "name": _dialog_name(entity, chat)},
            "message": message_to_dict(message),
        }
    raise NotFoundError(f"no messages found: {chat!r}")


def to_rows(data: dict) -> list[tuple]:
    messages = data["messages"] if "messages" in data else [data["message"]]
    return [
        (
            message["id"],
            message["date"],
            message["from"]["name"],
            _sanitize_text(message["text"]),
        )
        for message in messages
    ]


def _sanitize_text(text: str) -> str:
    return "".join(char if char.isprintable() else " " for char in text)
