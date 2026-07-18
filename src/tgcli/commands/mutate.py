"""Preview-to-commit mutations on existing messages (ADR-0028)."""

from tgcli import chatref, safety
from tgcli.commands.read import sanitize_plain_text
from tgcli.errors import NotFoundError


async def _entity(tg, chat: str):
    try:
        return await tg.get_entity(chatref.parse(chat))
    except ValueError:
        raise NotFoundError(f"dialog not found: {chat!r}") from None


async def _message(tg, entity, message_id: int):
    message = await tg.get_messages(entity, ids=message_id)
    if message is None:
        raise NotFoundError(f"message not found: {message_id}")
    return message


async def prepare_edit(tg, chat: str, message_id: int, text: str) -> dict:
    entity = await _entity(tg, chat)
    message = await _message(tg, entity, message_id)
    stored = safety.create_preview(
        {
            "kind": "edit",
            "chat": chat,
            "message_id": message_id,
            "old_text": message.text or "",
            "text": text,
        }
    )
    keys = ("preview_id", "message_id", "old_text", "text", "expires_at")
    return {key: stored[key] for key in keys}


async def commit_edit(tg, preview_id: str, payload: dict) -> dict:
    message = await tg.edit_message(
        payload["chat"], payload["message_id"], payload["text"]
    )
    return {"preview_id": preview_id, "message_id": message.id}


async def prepare_delete(tg, chat: str, message_id: int) -> dict:
    entity = await _entity(tg, chat)
    message = await _message(tg, entity, message_id)
    stored = safety.create_preview(
        {
            "kind": "delete",
            "chat": chat,
            "message_id": message_id,
            "text": message.text or "",
        }
    )
    keys = ("preview_id", "message_id", "text", "expires_at")
    return {key: stored[key] for key in keys}


async def commit_delete(tg, preview_id: str, payload: dict) -> dict:
    await tg.delete_messages(payload["chat"], [payload["message_id"]], revoke=True)
    return {"preview_id": preview_id, "message_id": payload["message_id"]}


def to_rows(data: dict) -> list[tuple]:
    if "old_text" in data:
        return [
            (
                data["preview_id"],
                data["message_id"],
                sanitize_plain_text(data["old_text"]),
                sanitize_plain_text(data["text"]),
            )
        ]
    if "text" in data:
        return [
            (
                data["preview_id"],
                data["message_id"],
                sanitize_plain_text(data["text"]),
            )
        ]
    return [(data["preview_id"], data["message_id"])]
