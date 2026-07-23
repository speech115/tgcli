import csv
import json
import os
import tempfile
from contextlib import contextmanager
from pathlib import Path

from tgcli import chatref
from tgcli.commands.read import _dialog_name, message_to_dict
from tgcli.errors import ExportError, NotFoundError


SUBSCRIBER_COLUMNS = ("id", "username", "first_name", "last_name", "phone", "is_bot")
TAKEOUT_MESSAGE_KWARGS = {"chats": True, "megagroups": True, "channels": True}

# Telegram caps ``channels.getParticipants`` at 200 rows per query for broadcast
# channels, so a single pass only ever sees the 200 most recent members. To get
# everyone we union saturating prefix searches (see _iter_all_channel_members).
_PARTICIPANTS_PAGE = 200
_SEARCH_REFINE_ALPHABET = (
    "abcdefghijklmnopqrstuvwxyz0123456789абвгдеёжзийклмнопрстуфхцчшщъыьэюя"
)


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
        raise ExportError(f"cannot write export to {destination}: {exc}") from exc
    finally:
        temporary.unlink(missing_ok=True)


async def _resolve_entity(tg, chat: str):
    try:
        return await tg.get_entity(chatref.parse(chat))
    except ValueError:
        raise NotFoundError(f"dialog not found: {chat!r}") from None


def _summary(
    kind: str, export_format: str, destination: Path, count: int, entity, chat: str
) -> dict:
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


async def export_messages(
    tg, chat: str, destination: Path, limit: int | None = None
) -> dict:
    entity = await _resolve_entity(tg, chat)
    count = 0
    with _atomic_text_destination(destination) as handle:
        async with _message_takeout(tg) as takeout:
            async for message in takeout.iter_messages(
                entity, limit=limit, reverse=True
            ):
                handle.write(
                    json.dumps(message_to_dict(message), ensure_ascii=False) + "\n"
                )
                count += 1
    return _summary("messages", "jsonl", destination, count, entity, chat)


def _subscriber_to_row(subscriber) -> dict:
    return {
        "id": subscriber.id,
        "username": _csv_cell(getattr(subscriber, "username", None)),
        "first_name": _csv_cell(getattr(subscriber, "first_name", None)),
        "last_name": _csv_cell(getattr(subscriber, "last_name", None)),
        "phone": getattr(subscriber, "phone", None) or "",
        "is_bot": str(bool(getattr(subscriber, "bot", False))),
    }


def _csv_cell(value: str | None) -> str:
    value = value or ""
    return f"'{value}" if value.startswith(("=", "+", "-", "@")) else value


async def _channel_member_total(tg, entity) -> int | None:
    from telethon.tl import functions

    try:
        full = await tg(functions.channels.GetFullChannelRequest(entity))
        return full.full_chat.participants_count
    except Exception:
        return None


async def _iter_all_channel_members(tg, entity):
    """Return every participant of a broadcast channel.

    Telegram caps ``channels.getParticipants`` at 200 rows per query for
    broadcast channels, so we union saturating prefix searches: start from the
    empty query and, whenever a query fills a full page, refine it with an extra
    character. Requests go through the raw ``GetParticipantsRequest`` (Telethon's
    ``iter_participants`` fires a second request per query just to compute a
    total we don't need) and run sequentially — issuing them concurrently trips
    the server's flood-wait, which is far slower than a steady ~0.4s/query. We
    stop as soon as we have seen the reported member total.
    """
    from telethon.tl import functions, types

    input_entity = await tg.get_input_entity(entity)
    total = await _channel_member_total(tg, entity)
    seen: dict[int, object] = {}

    async def query(prefix):
        result = await tg(
            functions.channels.GetParticipantsRequest(
                channel=input_entity,
                filter=types.ChannelParticipantsSearch(prefix),
                offset=0,
                limit=_PARTICIPANTS_PAGE,
                hash=0,
            )
        )
        return result.users

    pending = [""]
    while pending:
        prefix = pending.pop()
        users = await query(prefix)
        for user in users:
            seen[user.id] = user
        if len(users) >= _PARTICIPANTS_PAGE:
            pending.extend(prefix + ch for ch in _SEARCH_REFINE_ALPHABET)
        if total is not None and len(seen) >= total:
            break
    return list(seen.values())


async def export_subscribers(
    tg, channel: str, destination: Path, limit: int | None = None
) -> dict:
    entity = await _resolve_entity(tg, channel)
    count = 0
    want_all = limit is None or limit > _PARTICIPANTS_PAGE
    aggressive = want_all and getattr(entity, "broadcast", False)
    with _atomic_text_destination(destination) as handle:
        writer = csv.DictWriter(
            handle, fieldnames=SUBSCRIBER_COLUMNS, lineterminator="\n"
        )
        writer.writeheader()
        if aggressive:
            members = await _iter_all_channel_members(tg, entity)
            if limit is not None:
                members = members[:limit]
            for subscriber in members:
                writer.writerow(_subscriber_to_row(subscriber))
                count += 1
        else:
            async for subscriber in tg.iter_participants(entity, limit=limit):
                writer.writerow(_subscriber_to_row(subscriber))
                count += 1
    return _summary("subscribers", "csv", destination, count, entity, channel)


def to_rows(data: dict) -> list[tuple]:
    export = data["export"]
    return [(export["kind"], export["format"], export["path"], export["count"])]
