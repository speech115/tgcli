"""Preview and replay the intentional Telegram send surface (ADR-0028)."""

import secrets
from pathlib import Path

from tgcli import chatref, safety
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


async def commit(tg, preview_id: str, payload: dict) -> dict:
    message = await tg.send_message(chatref.parse(payload["chat"]), payload["text"])
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
                data["file"],
                data["reply_to"],
            )
        ]
    return [(data["preview_id"], data["message_id"])]
