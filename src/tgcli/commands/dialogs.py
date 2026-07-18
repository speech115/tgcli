def _kind(dialog) -> str:
    if dialog.is_channel:
        return "channel"
    if dialog.is_group:
        return "group"
    return "user"


async def fetch_dialogs(
    tg, limit: int = 50, *, unread_only: bool = False, kind: str | None = None
) -> dict:
    dialogs = []
    async for dialog in tg.iter_dialogs():
        mentions = (
            getattr(getattr(dialog, "dialog", None), "unread_mentions_count", 0) or 0
        )
        if unread_only and not (dialog.unread_count or mentions):
            continue
        if kind is not None and _kind(dialog) != kind:
            continue
        dialogs.append(
            {
                "id": dialog.id,
                "name": dialog.name,
                "kind": _kind(dialog),
                "username": getattr(dialog.entity, "username", None),
                "unread": dialog.unread_count,
                "mentions": mentions,
                "last_message_at": dialog.date.isoformat() if dialog.date else None,
            }
        )
        if len(dialogs) >= limit:
            break
    return {"dialogs": dialogs}


def to_rows(data: dict) -> list[tuple]:
    return [
        (
            dialog["id"],
            dialog["kind"],
            dialog["username"],
            dialog["name"],
            dialog["unread"],
            dialog["mentions"],
        )
        for dialog in data["dialogs"]
    ]
