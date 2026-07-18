from tgcli import chatref
from tgcli.commands.search import _sanitize_text
from tgcli.errors import NotFoundError
from telethon.tl import functions


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
        for part in (
            getattr(entity, "first_name", None),
            getattr(entity, "last_name", None),
        )
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


def _role(entity) -> str | None:
    if _kind(entity) == "user":
        return None
    if getattr(entity, "creator", False):
        return "creator"
    if getattr(entity, "admin_rights", None) is not None:
        return "admin"
    return "member"


def _can(entity, flag: str) -> bool | None:
    if _kind(entity) == "user":
        return True
    if getattr(entity, "broadcast", False):
        return False
    banned = getattr(entity, "banned_rights", None) or getattr(
        entity, "default_banned_rights", None
    )
    if banned is None:
        return None
    return not getattr(banned, flag, False)


def _capabilities(entity) -> dict[str, bool | None]:
    if getattr(entity, "creator", False):
        return {
            "send_messages": True,
            "send_media": True,
            "pin_messages": True,
            "delete_messages": True,
            "edit_messages": True,
        }
    admin = getattr(entity, "admin_rights", None)
    if admin is not None:
        can_post = (
            bool(getattr(admin, "post_messages", False))
            if getattr(entity, "broadcast", False)
            else True
        )
        return {
            "send_messages": can_post,
            "send_media": can_post,
            "pin_messages": bool(getattr(admin, "pin_messages", False)),
            "delete_messages": bool(getattr(admin, "delete_messages", False)),
            "edit_messages": bool(getattr(admin, "edit_messages", False)),
        }
    return {
        "send_messages": _can(entity, "send_messages"),
        "send_media": _can(entity, "send_media"),
        "pin_messages": _can(entity, "pin_messages"),
        "delete_messages": _kind(entity) == "user",
        "edit_messages": False,
    }


async def fetch_info_full(tg, chat: str) -> dict:
    entity = await _entity(tg, chat)
    base = await fetch_info(tg, chat)
    full = None
    if getattr(entity, "broadcast", False) or getattr(entity, "megagroup", False):
        response = await tg(functions.channels.GetFullChannelRequest(channel=entity))
        full = response.full_chat
    return {
        **base,
        "role": _role(entity),
        "can": _capabilities(entity),
        "slowmode_seconds": getattr(full, "slowmode_seconds", None),
        "participants_count": getattr(full, "participants_count", None),
        "about": getattr(full, "about", None),
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
