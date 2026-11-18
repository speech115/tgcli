import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from pathlib import Path

from tgcli import atomic, chatref
from tgcli.errors import ExportError, NotFoundError
from tgcli.messages import dialog_name, message_to_dict

TAKEOUT_MESSAGE_KWARGS = {"chats": True, "megagroups": True, "channels": True}


@contextmanager
def _atomic_text_destination(destination: Path, *, preserve_existing: bool = False):
    try:
        fd, temporary_name = tempfile.mkstemp(
            dir=destination.parent,
            prefix=f".{destination.name}.",
            suffix=".tmp",
        )
    except OSError as exc:
        raise ExportError(f"cannot write export to {destination}: {exc}") from exc

    temporary = Path(temporary_name)
    try:
        with os.fdopen(fd, "wb") as raw_handle:
            if preserve_existing and destination.exists():
                with destination.open("rb") as source:
                    shutil.copyfileobj(source, raw_handle)
        with temporary.open("a", encoding="utf-8", newline="") as handle:
            yield handle
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, destination)
        atomic.fsync_directory(destination.parent)
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
    kind: str,
    export_format: str,
    destination: Path,
    count: int,
    entity,
    chat: str,
    *,
    after_id: int | None = None,
    appended: bool = False,
) -> dict:
    payload = {
        "export": {
            "kind": kind,
            "format": export_format,
            "path": str(destination),
            "count": count,
            "dialog": {"id": entity.id, "name": dialog_name(entity, chat)},
        }
    }
    if after_id is not None or appended:
        payload["export"]["after_id"] = after_id
        payload["export"]["appended"] = appended
    return payload


def _message_takeout(tg):
    takeout_id = tg.session.takeout_id
    if type(takeout_id) is int:
        return tg.takeout()
    if takeout_id is not None:
        tg.session.takeout_id = None
    return tg.takeout(**TAKEOUT_MESSAGE_KWARGS)


def _resume_after_id(destination: Path) -> int:
    if not destination.exists():
        raise ExportError(f"cannot resume: missing export file {destination}")
    try:
        text = destination.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise ExportError(f"cannot resume: cannot read {destination}: {exc}") from exc
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        raise ExportError(f"cannot resume: empty export file {destination}")
    try:
        last = json.loads(lines[-1])
    except json.JSONDecodeError as exc:
        raise ExportError(
            f"cannot resume: corrupt last JSONL line in {destination}"
        ) from exc
    message_id = last.get("id") if isinstance(last, dict) else None
    if type(message_id) is not int or isinstance(message_id, bool) or message_id <= 0:
        raise ExportError(
            f"cannot resume: last JSONL line in {destination} has no valid id"
        )
    return message_id


async def export_messages(
    tg,
    chat: str,
    destination: Path,
    limit: int | None = None,
    *,
    after_id: int | None = None,
    append: bool = False,
    resume: bool = False,
) -> dict:
    from tgcli.errors import PolicyError

    if append and not resume and after_id is None:
        raise PolicyError("--append requires --after-id or --resume")
    if resume:
        after_id = _resume_after_id(destination)
        append = True

    entity = await _resolve_entity(tg, chat)
    count = 0
    min_id = after_id or 0

    if append:
        destination.parent.mkdir(parents=True, exist_ok=True)
    with _atomic_text_destination(destination, preserve_existing=append) as handle:
        async with _message_takeout(tg) as takeout:
            # A takeout session lifts the history limits, which is what it
            # is for: its pages need no gap.
            async for message in takeout.iter_messages(
                entity, limit=limit, reverse=True, min_id=min_id, wait_time=0
            ):
                handle.write(
                    json.dumps(message_to_dict(message, entity), ensure_ascii=False)
                    + "\n"
                )
                count += 1

    return _summary(
        "messages",
        "jsonl",
        destination,
        count,
        entity,
        chat,
        after_id=after_id,
        appended=append,
    )


def to_rows(data: dict) -> list[tuple]:
    export = data["export"]
    return [(export["kind"], export["format"], export["path"], export["count"])]
