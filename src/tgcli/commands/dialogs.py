def _kind(dialog) -> str:
    if dialog.is_channel:
        return "channel"
    if dialog.is_group:
        return "group"
    return "user"


async def fetch_dialogs(tg, limit: int = 50) -> dict:
    dialogs = []
    async for dialog in tg.iter_dialogs(limit=limit):
        dialogs.append(
            {
                "id": dialog.id,
                "name": dialog.name,
                "kind": _kind(dialog),
                "username": getattr(dialog.entity, "username", None),
                "unread": dialog.unread_count,
                "last_message_at": dialog.date.isoformat() if dialog.date else None,
            }
        )
    return {"dialogs": dialogs}


def to_rows(data: dict) -> list[tuple]:
    return [
        (dialog["id"], dialog["kind"], dialog["username"], dialog["name"], dialog["unread"])
        for dialog in data["dialogs"]
    ]
