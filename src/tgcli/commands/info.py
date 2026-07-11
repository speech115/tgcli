from tgcli import chatref
from tgcli.commands.search import _sanitize_text
from tgcli.errors import NotFoundError


async def _entity(tg, chat: str):
    try:
        return await tg.get_entity(chatref.parse(chat))
    except ValueError:
        raise NotFoundError(f"dialog not found: {chat!r}") from None


def _name(entity, fallback: str) -> str:
    title = getattr(entity, "title", None)
    if title:
        return title
    name = " ".join(
        part
        for part in (getattr(entity, "first_name", None), getattr(entity, "last_name", None))
        if part
    )
    return name or getattr(entity, "username", None) or fallback


def _kind(entity) -> str:
    if getattr(entity, "broadcast", False):
        return "channel"
    if getattr(entity, "megagroup", False) or getattr(entity, "title", None):
        return "group"
    return "user"


async def fetch_info(tg, chat: str) -> dict:
    entity = await _entity(tg, chat)
    return {
        "id": entity.id,
        "name": _name(entity, chat),
        "kind": _kind(entity),
        "username": getattr(entity, "username", None),
    }


async def fetch_count(tg, chat: str) -> dict:
    entity = await _entity(tg, chat)
    messages = await tg.get_messages(entity, limit=0)
    return {
        "dialog": {"id": entity.id, "name": _name(entity, chat)},
        "count": messages.total,
    }


def to_rows(data: dict) -> list[tuple]:
    if "count" in data:
        return [(data["count"],)]
    return [
        (
            data["id"],
            data["kind"],
            _sanitize_text(data["username"] or ""),
            _sanitize_text(data["name"]),
        )
    ]
