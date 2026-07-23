"""Preview and replay the intentional Telegram send surface (ADR-0028)."""

import hashlib
import mimetypes
import secrets
import tempfile
from pathlib import Path
from typing import cast

from telethon.tl import functions, types

from tgcli import chatref, formatting, safety
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


def _file_fingerprint(path: Path, snapshot: Path | None = None) -> tuple[int, str]:
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as source:
        destination = snapshot.open("xb") if snapshot is not None else None
        try:
            while chunk := source.read(1024 * 1024):
                digest.update(chunk)
                size += len(chunk)
                if destination is not None:
                    destination.write(chunk)
        finally:
            if destination is not None:
                destination.close()
    return size, digest.hexdigest()


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
    fmt: str = "md",
) -> dict:
    path = None
    if file is not None:
        if text is not None:
            raise PolicyError("send --file takes --caption, not positional text")
        path = Path(file).expanduser()
        if not path.is_file():
            raise NotFoundError(f"file not found: {file}")
        path = path.resolve()
        file_size, file_sha256 = _file_fingerprint(path)
        body = caption or ""
    else:
        if caption is not None:
            raise PolicyError("send --caption requires --file")
        if text is None:
            raise PolicyError("send requires TEXT or --file")
        file_size, file_sha256 = None, None
        body = text

    formatting.render(body, fmt)  # validate format early; raises on unknown fmt
    entity = await tg.get_entity(chatref.parse(chat))
    stored = safety.create_preview(
        {
            "kind": "send",
            "chat": chat,
            "text": body,
            "format": fmt,
            "file": str(path) if path else None,
            "file_size": file_size,
            "file_sha256": file_sha256,
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
        "format",
        "file",
        "file_size",
        "file_sha256",
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


def _verified_file_snapshot(payload: dict, directory: str) -> Path:
    source = Path(payload["file"])
    snapshot = Path(directory) / "upload"
    try:
        if not source.is_absolute():
            raise PolicyError("preview file no longer matches the prepared file")
        size, digest = _file_fingerprint(source, snapshot)
        if size != payload.get("file_size") or digest != payload.get("file_sha256"):
            raise PolicyError("preview file no longer matches the prepared file")
    except OSError:
        raise PolicyError("preview file no longer matches the prepared file") from None
    return snapshot


async def commit(tg, preview_id: str, payload: dict) -> dict:
    peer = await tg.get_input_entity(chatref.parse(payload["chat"]))
    random_id = payload["random_id"]
    message, entities = formatting.render(payload["text"], payload.get("format", "md"))
    common = {
        "peer": peer,
        "message": message,
        "entities": entities,
        "random_id": random_id,
        "silent": payload.get("silent") or None,
        "reply_to": _reply_header(payload),
    }
    if payload.get("file"):
        original_path = payload["file"]
        snapshot_dir = tempfile.TemporaryDirectory(prefix="tgcli-send-")
        try:
            snapshot = _verified_file_snapshot(payload, snapshot_dir.name)
            uploaded = await tg.upload_file(str(snapshot))
            request = functions.messages.SendMediaRequest(
                **common, media=_uploaded_media(uploaded, original_path)
            )
            response = await tg(request)
            [message_id] = confirmed_ids(response, [random_id])
        finally:
            snapshot_dir.cleanup()
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
                data.get("format", "md"),
            )
        ]
    return [(data["preview_id"], data["message_id"])]
