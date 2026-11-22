"""`tg media download` over many messages, and `tg media manifest`."""

from pathlib import Path

from telethon import errors as telethon_errors

from tgcli import chatref, pace
from tgcli.commands import media
from tgcli.errors import (
    NotFoundError,
    PartialFailure,
    PolicyError,
    RateLimitError,
    TgcliError,
)
from tgcli.messages import dialog_name, media_kind
from tgcli.tg_errors import FLOOD_ERRORS

BULK_DOWNLOAD_CAP = 100


def parse_message_ids(raw: str) -> list[int]:
    parts = [part.strip() for part in raw.split(",") if part.strip()]
    if not parts:
        raise PolicyError("--message-ids must list at least one id")
    ids: list[int] = []
    for part in parts:
        try:
            value = int(part)
        except ValueError as exc:
            raise PolicyError(f"invalid message id in --message-ids: {part!r}") from exc
        if value < 1:
            raise PolicyError(f"invalid message id in --message-ids: {part!r}")
        ids.append(value)
    if len(ids) > BULK_DOWNLOAD_CAP:
        raise PolicyError(
            f"--message-ids accepts at most {BULK_DOWNLOAD_CAP} ids (got {len(ids)})"
        )
    return ids


async def download_media_bulk(
    tg,
    chat: str,
    account_alias: str,
    *,
    message_ids: list[int] | None = None,
    kind: str | None = None,
    since=None,
    limit: int | None = None,
    output: str | None = None,
    progress=None,
) -> dict:
    """Download many media messages. Raises PartialFailure if any fail."""

    try:
        entity = await chatref.resolve(tg, chat)
    except ValueError:
        raise NotFoundError(f"dialog not found: {chat!r}") from None

    effective_limit = BULK_DOWNLOAD_CAP if limit is None else limit
    if effective_limit < 1:
        raise PolicyError("bulk media --limit must be positive")
    if effective_limit > BULK_DOWNLOAD_CAP:
        raise PolicyError(f"bulk media --limit may not exceed {BULK_DOWNLOAD_CAP}")

    if message_ids is not None and len(message_ids) > BULK_DOWNLOAD_CAP:
        raise PolicyError(
            f"bulk media accepts at most {BULK_DOWNLOAD_CAP} downloads per call"
        )

    output_dir = Path(output).expanduser() if output else Path.home() / "Downloads"
    output_dir.mkdir(parents=True, exist_ok=True)

    items = []
    failed = []
    skipped = []
    hard_error = None
    between_files = pace.Pace(pace.MEDIA_INTERVAL_S)
    async for source, target, resolve_error in _iter_bulk_candidates(
        tg,
        entity,
        chat,
        account_alias,
        message_ids=message_ids,
        kind=kind,
        since=since,
    ):
        if len(items) >= effective_limit:
            break
        if resolve_error is not None:
            failed.append(
                {"message_id": source.message_id, "error": str(resolve_error)}
            )
            continue
        if target is None:
            continue
        fallback_id = (
            source.story_id if source.story_id is not None else source.message_id
        )
        if fallback_id is None:
            continue
        unprefixed = media.safe_filename(target.filename, fallback_id)
        name = f"{fallback_id}-{unprefixed}"
        media._adopt_checkpoint(
            source,
            [output_dir / unprefixed, output_dir / f"media-{fallback_id}.bin"],
            output_dir / name,
        )
        try:
            await between_files.wait()
            result = await media.download_media(
                tg,
                source,
                account_alias,
                output=str(output_dir / name),
                progress=progress,
            )
            items.append(
                {
                    "message_id": source.message_id,
                    "path": result["path"],
                    "bytes": result["bytes"],
                    "resumed": result["resumed"],
                }
            )
        except NotFoundError as exc:
            failed.append({"message_id": source.message_id, "error": str(exc)})
        except PolicyError as exc:
            if str(exc).startswith("output path already exists: "):
                skipped.append({"message_id": source.message_id, "reason": str(exc)})
                continue
            hard_error = exc
            failed.append({"message_id": source.message_id, "error": str(exc)})
            break
        except FLOOD_ERRORS as exc:
            hard_error = exc
            failed.append({"message_id": source.message_id, "error": str(exc)})
            break
        except telethon_errors.UnauthorizedError:
            raise
        except Exception as exc:
            hard_error = exc
            failed.append({"message_id": source.message_id, "error": str(exc)})
            break

    data = {
        "dialog": {"id": entity.id, "name": dialog_name(entity, chat)},
        "items": items,
        "count": len(items),
        "failed": failed,
        "skipped": skipped,
    }
    if failed or hard_error is not None:
        if isinstance(hard_error, FLOOD_ERRORS):
            cause = RateLimitError(
                f"rate limited for {hard_error.seconds}s",
                retry_after=hard_error.seconds,
            )
        elif isinstance(hard_error, TgcliError):
            cause = hard_error
        elif hard_error is not None:
            cause = TgcliError(str(hard_error))
        else:
            cause = NotFoundError("one or more media messages were not found")
        raise PartialFailure(
            f"bulk media download finished with {len(failed)} failure(s)",
            data,
            cause=cause,
            rows=bulk_to_rows(data),
        )
    return data


async def _iter_bulk_candidates(
    tg,
    entity,
    chat: str,
    account_alias: str,
    *,
    message_ids: list[int] | None,
    kind: str | None,
    since,
):
    """Yield ``(source, target, resolve_error)`` rows for bulk download.

    Candidate-resolution ``NotFoundError`` is yielded as ``resolve_error`` so
    the caller can record an additive ``failed`` row and continue the batch.
    """
    if message_ids is not None:
        for message_id in message_ids:
            source = media.MediaSource(
                chat=chat, message_id=message_id, private_channel_id=None
            )
            try:
                if kind is not None or since is not None:
                    message = await tg.get_messages(entity, ids=message_id)
                    if message is None:
                        raise NotFoundError(f"message not found: {message_id}")
                    if kind is not None and media_kind(message) != kind:
                        continue
                    if (
                        since is not None
                        and message.date is not None
                        and message.date < since
                    ):
                        continue
                    target = media._DownloadTarget(
                        media=message.media,
                        filename=media._message_filename(message, message_id),
                        size=media._message_size(message),
                    )
                else:
                    _, target = await media.resolve_message(tg, source, account_alias)
            except NotFoundError as exc:
                yield source, None, exc
                continue
            yield source, target, None
        return

    async for message in tg.iter_messages(
        entity, limit=None, wait_time=pace.PAGE_WAIT_S
    ):
        if since is not None and message.date is not None and message.date < since:
            break
        if kind is not None and media_kind(message) != kind:
            continue
        if not getattr(message, "media", None):
            continue
        yield (
            media.MediaSource(
                chat=chat, message_id=message.id, private_channel_id=None
            ),
            media._DownloadTarget(
                media=message.media,
                filename=media._message_filename(message, message.id),
                size=media._message_size(message),
            ),
            None,
        )


def bulk_to_rows(data: dict) -> list[tuple]:
    """One frozen `media download` row per downloaded item (the CLI contract)."""
    return [(item["path"], item["bytes"], item["resumed"], 1) for item in data["items"]]


def _manifest_item(message, kind: str) -> dict:
    file = getattr(message, "file", None)
    return {
        "message_id": message.id,
        "type": kind,
        "size": getattr(file, "size", None) if file else None,
        "mime": getattr(file, "mime_type", None) if file else None,
        "filename": getattr(file, "name", None) if file else None,
    }


async def manifest(
    tg,
    source: str,
    *,
    kind: str | None = None,
    since=None,
    limit: int = 100,
) -> dict:

    try:
        entity = await chatref.resolve(tg, source)
    except ValueError:
        raise NotFoundError(f"dialog not found: {source!r}") from None

    items = []
    async for message in tg.iter_messages(
        entity, limit=limit, wait_time=pace.INTERACTIVE_PAGE_WAIT
    ):
        if since is not None and message.date is not None and message.date < since:
            break
        found = media_kind(message)
        if found is None:
            continue
        if kind is not None and found != kind:
            continue
        items.append(_manifest_item(message, found))

    return {
        "dialog": {"id": entity.id, "name": dialog_name(entity, source)},
        "items": items,
        "count": len(items),
    }


def manifest_to_rows(data: dict) -> list[tuple]:
    return [
        (
            item["message_id"],
            item["type"],
            item["size"],
            item["mime"] or "",
            item["filename"] or "",
        )
        for item in data["items"]
    ]
