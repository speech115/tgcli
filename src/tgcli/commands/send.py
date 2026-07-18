"""Preview and replay the intentional Telegram send surface (ADR-0028)."""

import mimetypes
import secrets
from pathlib import Path
from typing import cast

from telethon.tl import functions, types

from tgcli import chatref, safety
from tgcli.confirm import confirmed_ids
from tgcli.errors import NotFoundError, PolicyError


def _target_to_dict(entity) -> dict:
    name = (
        getattr(entity, "title", None)
        or getattr(entity, "first_name", None)
        or getattr(entity, "username", None)
        or str(getattr(entity, "id", ""))
    )
    return {"id": entity.id, "name": name}


def _random_id() -> int:
    return secrets.randbelow(2**63 - 1) + 1


async def prepare(
    tg,
    chat: str,
    text: str | None = None,
    *,
    reply_to: int | None = None,
    file: str | None = None,
    caption: str | None = None,
    topic: int | None = None,
    silent: bool = False,
) -> dict:
    path = None
    if file is not None:
        if text is not None:
            raise PolicyError("send --file takes --caption, not positional text")
        path = Path(file).expanduser()
        if not path.is_file():
            raise NotFoundError(f"file not found: {file}")
        path = path.resolve()
        body = caption or ""
    else:
        if caption is not None:
            raise PolicyError("send --caption requires --file")
        if text is None:
            raise PolicyError("send requires TEXT or --file")
        body = text

    entity = await tg.get_entity(chatref.parse(chat))
    stored = safety.create_preview(
        {
            "kind": "send",
            "chat": chat,
            "text": body,
            "file": str(path) if path else None,
            "file_size": path.stat().st_size if path else None,
            "reply_to": reply_to,
            "topic": topic,
            "silent": silent,
            "random_id": _random_id(),
            "to": _target_to_dict(entity),
        }
    )
    keys = (
        "preview_id",
        "to",
        "text",
        "file",
        "file_size",
        "reply_to",
        "topic",
        "silent",
        "expires_at",
    )
    return {key: stored[key] for key in keys}


def _reply_header(payload: dict):
    reply_to, topic = payload.get("reply_to"), payload.get("topic")
    if reply_to is None and topic is None:
        return None
    return types.InputReplyToMessage(
        reply_to_msg_id=cast(int, reply_to if reply_to is not None else topic),
        top_msg_id=cast(int, topic)
        if reply_to is not None and topic is not None
        else None,
    )


def _uploaded_media(uploaded, file: str):
    mime = mimetypes.guess_type(file)[0] or "application/octet-stream"
    if mime in ("image/jpeg", "image/png"):
        return types.InputMediaUploadedPhoto(file=uploaded)
    return types.InputMediaUploadedDocument(
        file=uploaded,
        mime_type=mime,
        attributes=[types.DocumentAttributeFilename(Path(file).name)],
    )


async def commit(tg, preview_id: str, payload: dict) -> dict:
    peer = await tg.get_input_entity(chatref.parse(payload["chat"]))
    random_id = payload["random_id"]
    common = {
        "peer": peer,
        "message": payload["text"],
        "random_id": random_id,
        "silent": payload.get("silent") or None,
        "reply_to": _reply_header(payload),
    }
    if file := payload.get("file"):
        uploaded = await tg.upload_file(file)
        request = functions.messages.SendMediaRequest(
            **common, media=_uploaded_media(uploaded, file)
        )
    else:
        request = functions.messages.SendMessageRequest(**common)
    response = await tg(request)
    [message_id] = confirmed_ids(response, [random_id])
    return {"preview_id": preview_id, "message_id": message_id}


def to_rows(data: dict) -> list[tuple]:
    if "to" in data:
        return [
            (
                data["preview_id"],
                data["to"]["id"],
                data["to"]["name"],
                data["text"],
                data["expires_at"],
                data["file"],
                data["reply_to"],
            )
        ]
    return [(data["preview_id"], data["message_id"])]
