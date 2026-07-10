"""Preview and replay the small, intentional Telegram send surface."""

from tgcli import safety


def _target_to_dict(entity) -> dict:
    name = (
        getattr(entity, "title", None)
        or getattr(entity, "first_name", None)
        or getattr(entity, "username", None)
        or str(getattr(entity, "id", ""))
    )
    return {"id": entity.id, "name": name}


async def prepare(tg, chat: str, text: str) -> dict:
    entity = await tg.get_entity(chat)
    stored = safety.create_preview(
        {"chat": chat, "text": text, "to": _target_to_dict(entity)}
    )
    return {
        "preview_id": stored["preview_id"],
        "to": stored["to"],
        "text": stored["text"],
        "expires_at": stored["expires_at"],
    }


async def commit(tg, preview_id: str, payload: dict) -> dict:
    message = await tg.send_message(payload["chat"], payload["text"])
    return {"preview_id": preview_id, "message_id": message.id}


def to_rows(data: dict) -> list[tuple]:
    if "to" in data:
        return [
            (
                data["preview_id"],
                data["to"]["id"],
                data["to"]["name"],
                data["text"],
                data["expires_at"],
            )
        ]
    return [(data["preview_id"], data["message_id"])]
