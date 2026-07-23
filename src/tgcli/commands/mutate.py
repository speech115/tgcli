"""Preview-to-commit mutations on existing messages (ADR-0028)."""

import secrets

from telethon.errors import MessageNotModifiedError
from telethon.tl import functions

from tgcli import chatref, formatting, safety
from tgcli.commands.read import sanitize_plain_text
from tgcli.confirm import confirmed_ids
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


def _random_id() -> int:
    return secrets.randbelow(2**63 - 1) + 1


async def prepare_edit(
    tg, chat: str, message_id: int, text: str, fmt: str = "plain"
) -> dict:
    formatting.render(text, fmt)  # validate format early; raises on unknown fmt
    entity = await _entity(tg, chat)
    message = await _message(tg, entity, message_id)
    stored = safety.create_preview(
        {
            "kind": "edit",
            "chat": chat,
            "message_id": message_id,
            "old_text": message.text or "",
            "text": text,
            "format": fmt,
        }
    )
    keys = ("preview_id", "message_id", "old_text", "text", "format", "expires_at")
    return {key: stored[key] for key in keys}


async def commit_edit(tg, preview_id: str, payload: dict) -> dict:
    body, entities = formatting.render(payload["text"], payload.get("format", "plain"))
    try:
        message = await tg.edit_message(
            chatref.parse(payload["chat"]),
            payload["message_id"],
            body,
            formatting_entities=entities,
            parse_mode=None,
        )
        message_id = message.id
    except MessageNotModifiedError:
        message_id = payload["message_id"]
    return {"preview_id": preview_id, "message_id": message_id}


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


async def prepare_forward(tg, source: str, message_id: int, destination: str) -> dict:
    source_entity = await _entity(tg, source)
    message = await _message(tg, source_entity, message_id)
    await _entity(tg, destination)
    stored = safety.create_preview(
        {
            "kind": "forward",
            "source": source,
            "message_id": message_id,
            "destination": destination,
            "text": message.text or "",
            "random_id": _random_id(),
        }
    )
    keys = (
        "preview_id",
        "source",
        "message_id",
        "destination",
        "text",
        "expires_at",
    )
    return {key: stored[key] for key in keys}


async def commit_forward(tg, preview_id: str, payload: dict) -> dict:
    response = await tg(
        functions.messages.ForwardMessagesRequest(
            from_peer=await tg.get_input_entity(chatref.parse(payload["source"])),
            id=[payload["message_id"]],
            random_id=[payload["random_id"]],
            to_peer=await tg.get_input_entity(chatref.parse(payload["destination"])),
        )
    )
    [message_id] = confirmed_ids(response, [payload["random_id"]])
    return {"preview_id": preview_id, "message_id": message_id}


async def mark_read(tg, chat: str) -> dict:
    entity = await _entity(tg, chat)
    await tg.send_read_acknowledge(entity)
    return {"dialog": {"id": entity.id}, "marked_read": True}


def to_rows(data: dict) -> list[tuple]:
    if "marked_read" in data:
        return [(data["dialog"]["id"], "read")]
    if "destination" in data:
        return [
            (
                data["preview_id"],
                data["source"],
                data["message_id"],
                data["destination"],
            )
        ]
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
