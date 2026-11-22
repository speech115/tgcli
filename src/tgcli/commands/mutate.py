"""Preview-to-commit mutations on existing messages."""

from telethon.errors import MessageNotModifiedError

from tgcli import chatref, formatting, safety
from tgcli.errors import NotFoundError
from tgcli.messages import sanitize_plain_text


async def _entity(tg, chat: str):
    try:
        return await chatref.resolve(tg, chat)
    except ValueError:
        raise NotFoundError(f"dialog not found: {chat!r}") from None


async def _input_peer(tg, chat: str, *, expected_peer_id: int | None = None):
    try:
        return await chatref.resolve_input(tg, chat, expected_peer_id=expected_peer_id)
    except ValueError:
        raise NotFoundError(f"dialog not found: {chat!r}") from None


async def _message(tg, entity, message_id: int):
    message = await tg.get_messages(entity, ids=message_id)
    if message is None:
        raise NotFoundError(f"message not found: {message_id}")
    return message


async def prepare_edit(
    tg, chat: str, message_id: int, text: str, fmt: str = "plain"
) -> dict:
    formatting.render(text, fmt)
    entity = await _entity(tg, chat)
    message = await _message(tg, entity, message_id)
    payload = {
        "kind": "edit",
        "chat": chat,
        "message_id": message_id,
        "old_text": message.text or "",
        "text": text,
        "format": fmt,
    }
    if (peer_id := chatref.marked_peer_id(entity)) is not None:
        payload["peer_id"] = peer_id
    stored = safety.create_preview(payload)
    keys = ("preview_id", "message_id", "old_text", "text", "format", "expires_at")
    return {key: stored[key] for key in keys}


async def commit_edit(tg, preview_id: str, payload: dict) -> dict:
    body, entities = formatting.render(payload["text"], payload.get("format", "plain"))
    peer = await _input_peer(
        tg, payload["chat"], expected_peer_id=payload.get("peer_id")
    )
    try:
        message = await tg.edit_message(
            peer,
            payload["message_id"],
            body,
            formatting_entities=entities,
            parse_mode=None,
        )
        message_id = message.id
    except MessageNotModifiedError:
        message_id = payload["message_id"]
    except ValueError:
        raise NotFoundError(f"dialog not found: {payload['chat']!r}") from None
    return {"preview_id": preview_id, "message_id": message_id}


async def prepare_delete(tg, chat: str, message_id: int) -> dict:
    entity = await _entity(tg, chat)
    message = await _message(tg, entity, message_id)
    payload = {
        "kind": "delete",
        "chat": chat,
        "message_id": message_id,
        "text": message.text or "",
    }
    if (peer_id := chatref.marked_peer_id(entity)) is not None:
        payload["peer_id"] = peer_id
    stored = safety.create_preview(payload)
    keys = ("preview_id", "message_id", "text", "expires_at")
    return {key: stored[key] for key in keys}


async def commit_delete(tg, preview_id: str, payload: dict) -> dict:
    peer = await _input_peer(
        tg, payload["chat"], expected_peer_id=payload.get("peer_id")
    )
    try:
        await tg.delete_messages(peer, [payload["message_id"]], revoke=True)
    except ValueError:
        raise NotFoundError(f"dialog not found: {payload['chat']!r}") from None
    return {"preview_id": preview_id, "message_id": payload["message_id"]}


def to_rows(data: dict) -> list[tuple]:
    if "old_text" in data:
        return [
            (
                data["preview_id"],
                data["message_id"],
                sanitize_plain_text(data["old_text"]),
                sanitize_plain_text(data["text"]),
                data.get("format", "plain"),
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
