import csv
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from tgcli.commands.read import _dialog_name, message_to_dict
from tgcli.errors import ExportError, NotFoundError


SUBSCRIBER_COLUMNS = ("id", "username", "first_name", "last_name", "phone", "is_bot")
TAKEOUT_MESSAGE_KWARGS = {"chats": True, "megagroups": True, "channels": True}


@contextmanager
def _atomic_text_destination(destination: Path):
    try:
        fd, temporary_name = tempfile.mkstemp(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
            text=True,
        )
    except OSError as exc:
        raise ExportError(f"cannot write export to {destination}: {exc}") from exc

    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as handle:
            yield handle
        os.replace(temporary, destination)
    except OSError as exc:
        temporary.unlink(missing_ok=True)
        raise ExportError(f"cannot write export to {destination}: {exc}") from exc
    except Exception:
        temporary.unlink(missing_ok=True)
        raise


async def _resolve_entity(tg, chat: str):
    try:
        return await tg.get_entity(chat)
    except ValueError:
        raise NotFoundError(f"dialog not found: {chat!r}") from None


def _summary(kind: str, export_format: str, destination: Path, count: int, entity, chat: str) -> dict:
    return {
        "export": {
            "kind": kind,
            "format": export_format,
            "path": str(destination),
            "count": count,
            "dialog": {"id": entity.id, "name": _dialog_name(entity, chat)},
        }
    }


def _message_takeout(tg):
    takeout_id = tg.session.takeout_id
    if type(takeout_id) is int:
        return tg.takeout()
    if takeout_id is not None:
        tg.session.takeout_id = None
    return tg.takeout(**TAKEOUT_MESSAGE_KWARGS)


async def export_messages(tg, chat: str, destination: Path, limit: int | None = None) -> dict:
    entity = await _resolve_entity(tg, chat)
    count = 0
    with _atomic_text_destination(destination) as handle:
        async with _message_takeout(tg) as takeout:
            async for message in takeout.iter_messages(entity, limit=limit, reverse=True):
                handle.write(json.dumps(message_to_dict(message), ensure_ascii=False) + "\n")
                count += 1
    return _summary("messages", "jsonl", destination, count, entity, chat)


def _subscriber_to_row(subscriber) -> dict:
    return {
        "id": subscriber.id,
        "username": getattr(subscriber, "username", None) or "",
        "first_name": getattr(subscriber, "first_name", None) or "",
        "last_name": getattr(subscriber, "last_name", None) or "",
        "phone": getattr(subscriber, "phone", None) or "",
        "is_bot": str(bool(getattr(subscriber, "bot", False))),
    }


async def export_subscribers(tg, channel: str, destination: Path, limit: int | None = None) -> dict:
    entity = await _resolve_entity(tg, channel)
    count = 0
    with _atomic_text_destination(destination) as handle:
        writer = csv.DictWriter(handle, fieldnames=SUBSCRIBER_COLUMNS, lineterminator="\n")
        writer.writeheader()
        async for subscriber in tg.iter_participants(entity, limit=limit):
            writer.writerow(_subscriber_to_row(subscriber))
            count += 1
    return _summary("subscribers", "csv", destination, count, entity, channel)


def to_rows(data: dict) -> list[tuple]:
    export = data["export"]
    return [(export["kind"], export["format"], export["path"], export["count"])]
