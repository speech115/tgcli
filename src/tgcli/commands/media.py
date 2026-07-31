"""Media download command helpers (Phase 3; Telethon-only)."""

import errno
import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path

from telethon import errors as telethon_errors, functions
from telethon.tl import types

from tgcli import atomic, chatref
from tgcli.errors import (
    NotFoundError,
    PartialFailure,
    PolicyError,
    RateLimitError,
    TgcliError,
)
from tgcli.session import state_dir
from tgcli.transfer import CHUNK_SIZE, PROGRESS_EVERY_CHUNKS, download_striped

PRIVATE_LINK = re.compile(r"(?:https?://)?t\.me/c/(\d+)/(\d+)/?$")
PUBLIC_LINK = re.compile(r"(?:https?://)?t\.me/([A-Za-z0-9_]+)/([1-9]\d*)/?$")

CHECKPOINT_EVERY_CHUNKS = 16
MAX_FILENAME_BYTES = 200


@dataclass(frozen=True)
class MediaSource:
    chat: str | None
    message_id: int
    private_channel_id: int | None


def parse_source(source: str, message_id: int | None) -> MediaSource:
    private_match = PRIVATE_LINK.fullmatch(source)
    if private_match:
        if message_id is not None:
            raise NotFoundError(f"invalid media source: {source!r}")
        return MediaSource(
            chat=None,
            message_id=int(private_match.group(2)),
            private_channel_id=int(private_match.group(1)),
        )

    public_match = PUBLIC_LINK.fullmatch(source)
    if public_match:
        if message_id is not None:
            raise NotFoundError(f"invalid media source: {source!r}")
        return MediaSource(
            chat=f"@{public_match.group(1)}",
            message_id=int(public_match.group(2)),
            private_channel_id=None,
        )

    if message_id is None or message_id < 1:
        raise NotFoundError(f"invalid media source: {source!r}")
    return MediaSource(chat=source, message_id=message_id, private_channel_id=None)


def safe_filename(name: str | None, message_id: int) -> str:
    candidate = Path((name or "").replace("\\", "/")).name
    candidate = "".join(char if char.isprintable() else " " for char in candidate)
    candidate = candidate.strip(" .")
    candidate = candidate.encode()[:MAX_FILENAME_BYTES].decode(errors="ignore")
    return candidate if candidate else f"media-{message_id}.bin"


def destination_for(name: str, requested: str | None) -> Path:
    path = (
        Path(requested).expanduser() if requested else Path.home() / "Downloads" / name
    )
    if path.exists():
        raise PolicyError(f"output path already exists: {path}")
    return path


async def _resolve_private_entity(tg, channel_id: int, account_alias: str):
    async for dialog in tg.iter_dialogs():
        entity = dialog.entity
        # A t.me/c/ link names a channel: ids are only unique within a peer
        # kind, so a user with the same number is a different peer.
        if isinstance(entity, types.Channel) and entity.id == channel_id:
            try:
                input_entity = await tg.get_input_entity(entity)
                await tg(functions.channels.GetChannelsRequest([input_entity]))
            except (
                ValueError,
                telethon_errors.ChannelInvalidError,
                telethon_errors.ChannelPrivateError,
            ):
                raise NotFoundError(
                    f"private channel {channel_id} not found; "
                    f"account {account_alias!r} lacks access"
                ) from None
            return entity
    raise NotFoundError(
        f"private channel {channel_id} not found; "
        f"account {account_alias!r} lacks access"
    )


async def resolve_message(tg, source: MediaSource, account_alias: str):
    try:
        entity = (
            await _resolve_private_entity(tg, source.private_channel_id, account_alias)
            if source.private_channel_id is not None
            else await tg.get_entity(chatref.parse(source.chat))  # type: ignore  # chat set when no private link
        )
    except ValueError:
        raise NotFoundError(f"dialog not found: {source.chat!r}") from None

    message = await tg.get_messages(entity, ids=source.message_id)
    if message is None or not getattr(message, "media", None):
        raise NotFoundError(f"downloadable media not found: {source.message_id}")
    return entity, message


def _source_label(source: MediaSource) -> str:
    chat = (
        f"private:{source.private_channel_id}"
        if source.private_channel_id
        else source.chat
    )
    return f"{chat}:{source.message_id}"


def _state_paths(source: MediaSource) -> tuple[Path, Path]:
    key = hashlib.sha256(_source_label(source).encode()).hexdigest()
    directory = state_dir() / "downloads"
    return directory / f"{key}.json", directory / f"{key}.part"


def _message_filename(message, message_id: int) -> str:
    file = getattr(message, "file", None)
    return safe_filename(getattr(file, "name", None), message_id)


def _message_size(message) -> int | None:
    file = getattr(message, "file", None)
    return getattr(file, "size", None)


def _write_state(
    path: Path,
    source: MediaSource,
    destination: Path,
    offset: int,
    *,
    resumable: bool = True,
) -> None:
    atomic.replace_text(
        path,
        json.dumps(
            {
                "source": _source_label(source),
                "destination": str(destination),
                "offset": offset,
                "resumable": resumable,
            }
        ),
    )


def _publish(part_path: Path, destination: Path) -> None:
    """Move a finished partial file onto its final path.

    The rename is atomic while both live on one filesystem — the case worth
    protecting. `--output` on another mount (the partial file sits under the
    state root) makes `os.replace` raise EXDEV; then copy into a sibling temp
    file and rename that inside the destination filesystem, so the final name
    never points at a half-written file.
    """
    try:
        os.replace(part_path, destination)
        return
    except OSError as exc:
        if exc.errno != errno.EXDEV:
            raise

    mode = part_path.stat().st_mode & 0o777
    fd, staged_name = tempfile.mkstemp(
        prefix=f".{destination.name}-", suffix=".tmp", dir=destination.parent
    )
    os.close(fd)
    staged = Path(staged_name)
    try:
        shutil.copyfile(part_path, staged)
        os.chmod(staged, mode)
        os.replace(staged, destination)
    except BaseException:
        staged.unlink(missing_ok=True)
        raise
    part_path.unlink(missing_ok=True)


def _resume_offset(
    state_path: Path, part_path: Path, source: MediaSource, destination: Path
) -> int:
    if not state_path.exists():
        # No state means no proven byte: discard the partial and start over
        # rather than wedging every later run on an unresumable file.
        part_path.unlink(missing_ok=True)
        return 0
    try:
        state = json.loads(state_path.read_text())
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise PolicyError(f"media download state is invalid: {state_path}") from exc
    if not isinstance(state, dict):
        raise PolicyError(f"media download state is invalid: {state_path}")
    if state.get("resumable") is False:
        # A parallel transfer writes its stripes at scattered offsets, so no
        # byte count describes what it already has: the state says so, and the
        # partial file is worth nothing. Drop both and restart from zero
        # instead of wedging the message on an unresumable leftover.
        part_path.unlink(missing_ok=True)
        state_path.unlink(missing_ok=True)
        return 0
    if not part_path.exists():
        raise PolicyError(f"media download state has no partial file: {part_path}")
    offset = state.get("offset")
    part_size = part_path.stat().st_size
    if (
        state.get("source") != _source_label(source)
        or state.get("destination") != str(destination)
        or not isinstance(offset, int)
        or offset < 0
        or offset > part_size
    ):
        raise PolicyError(
            f"media download state does not match requested output: {state_path}"
        )
    if offset < part_size:
        with part_path.open("r+b") as handle:
            handle.truncate(offset)
    return offset


async def download_media(
    tg,
    source: MediaSource,
    account_alias: str,
    *,
    output: str | None = None,
    parallel: int = 1,
    progress=None,
) -> dict:
    if parallel < 1:
        raise PolicyError("parallel media download count must be positive")

    _, message = await resolve_message(tg, source, account_alias)
    destination = destination_for(_message_filename(message, source.message_id), output)
    state_path, part_path = _state_paths(source)
    offset = _resume_offset(state_path, part_path, source, destination)
    resumed = offset > 0

    part_path.parent.mkdir(parents=True, exist_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if parallel > 1:
        if resumed:
            raise PolicyError(
                "parallel media download cannot resume an interrupted transfer"
            )
        _write_state(state_path, source, destination, 0, resumable=False)
        return await _download_parallel(
            tg,
            message,
            source,
            destination,
            state_path,
            part_path,
            parallel,
            progress,
        )

    if not resumed:
        _write_state(state_path, source, destination, offset)

    with part_path.open("ab" if resumed else "xb") as handle:
        current = offset
        chunks_since_checkpoint = 0
        chunks_since_progress = 0
        try:
            async for chunk in tg.iter_download(
                message.media, offset=offset, request_size=CHUNK_SIZE
            ):
                handle.write(bytes(chunk))
                current = handle.tell()
                chunks_since_checkpoint += 1
                chunks_since_progress += 1
                if chunks_since_checkpoint >= CHECKPOINT_EVERY_CHUNKS:
                    handle.flush()
                    _write_state(state_path, source, destination, current)
                    chunks_since_checkpoint = 0
                if progress and chunks_since_progress >= PROGRESS_EVERY_CHUNKS:
                    progress(current, _message_size(message))
                    chunks_since_progress = 0
        except BaseException:
            if chunks_since_checkpoint:
                handle.flush()
                _write_state(state_path, source, destination, current)
            raise
        if chunks_since_checkpoint:
            handle.flush()
            _write_state(state_path, source, destination, current)
        if progress and chunks_since_progress:
            progress(current, _message_size(message))

    _publish(part_path, destination)
    state_path.unlink(missing_ok=True)
    return {
        "source": _source_label(source),
        "path": str(destination),
        "bytes": destination.stat().st_size,
        "resumed": resumed,
        "parallel": 1,
    }


async def _download_parallel(
    tg,
    message,
    source: MediaSource,
    destination: Path,
    state_path: Path,
    part_path: Path,
    parallel: int,
    progress,
) -> dict:
    total = _message_size(message)
    if not isinstance(total, int) or total <= 0:
        raise NotFoundError(
            f"media size is unavailable for parallel download: {source.message_id}"
        )

    await download_striped(
        tg,
        message.media,
        part_path,
        size=total,
        parallel=parallel,
        progress=progress,
    )
    _publish(part_path, destination)
    state_path.unlink(missing_ok=True)
    return {
        "source": _source_label(source),
        "path": str(destination),
        "bytes": destination.stat().st_size,
        "resumed": False,
        "parallel": parallel,
    }


def to_rows(data: dict) -> list[tuple]:
    return [(data["path"], data["bytes"], data["resumed"], data["parallel"])]


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
    """Download many media messages (ADR-0032). Raises PartialFailure if any fail."""
    from tgcli.commands.read import _dialog_name

    try:
        entity = await tg.get_entity(chatref.parse(chat))
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
    async for source, message, resolve_error in _iter_bulk_candidates(
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
        try:
            result = await download_media(
                tg,
                source,
                account_alias,
                output=str(
                    output_dir
                    / _message_filename(
                        # download_media resolves again for transfer metadata
                        message,
                        source.message_id,
                    )
                ),
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
        except telethon_errors.FloodWaitError as exc:
            hard_error = exc
            failed.append({"message_id": source.message_id, "error": str(exc)})
            break
        except telethon_errors.SessionRevokedError:
            raise
        except Exception as exc:
            hard_error = exc
            failed.append({"message_id": source.message_id, "error": str(exc)})
            break

    data = {
        "dialog": {"id": entity.id, "name": _dialog_name(entity, chat)},
        "items": items,
        "count": len(items),
        "failed": failed,
        "skipped": skipped,
    }
    if failed or hard_error is not None:
        if isinstance(hard_error, telethon_errors.FloodWaitError):
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
    """Yield ``(source, message, resolve_error)`` rows for bulk download.

    Candidate-resolution ``NotFoundError`` is yielded as ``resolve_error`` so
    the caller can record an additive ``failed`` row and continue the batch.
    """
    if message_ids is not None:
        for message_id in message_ids:
            source = MediaSource(
                chat=chat, message_id=message_id, private_channel_id=None
            )
            try:
                if kind is not None or since is not None:
                    message = await tg.get_messages(entity, ids=message_id)
                    if message is None:
                        raise NotFoundError(f"message not found: {message_id}")
                    if kind is not None and _media_kind(message) != kind:
                        continue
                    if (
                        since is not None
                        and message.date is not None
                        and message.date < since
                    ):
                        continue
                else:
                    _, message = await resolve_message(tg, source, account_alias)
            except NotFoundError as exc:
                yield source, None, exc
                continue
            yield source, message, None
        return

    async for message in tg.iter_messages(entity, limit=None):
        if since is not None and message.date is not None and message.date < since:
            break
        if kind is not None and _media_kind(message) != kind:
            continue
        if not getattr(message, "media", None):
            continue
        yield (
            MediaSource(chat=chat, message_id=message.id, private_channel_id=None),
            message,
            None,
        )


def bulk_to_rows(data: dict) -> list[tuple]:
    """One frozen `media download` row per downloaded item (CONTRACT §5 TSV)."""
    return [(item["path"], item["bytes"], item["resumed"], 1) for item in data["items"]]


MEDIA_KINDS = ("photo", "video", "video_note", "audio", "voice", "document")


def _media_kind(message) -> str | None:
    if not getattr(message, "media", None):
        return None
    if getattr(message, "photo", None):
        return "photo"
    if getattr(message, "video_note", None):
        return "video_note"
    if getattr(message, "video", None):
        return "video"
    if getattr(message, "voice", None):
        return "voice"
    if getattr(message, "audio", None):
        return "audio"
    if getattr(message, "document", None):
        return "document"
    return None


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
    from tgcli.commands.read import _dialog_name

    try:
        entity = await tg.get_entity(chatref.parse(source))
    except ValueError:
        raise NotFoundError(f"dialog not found: {source!r}") from None

    items = []
    async for message in tg.iter_messages(entity, limit=limit):
        if since is not None and message.date is not None and message.date < since:
            break
        media_kind = _media_kind(message)
        if media_kind is None:
            continue
        if kind is not None and media_kind != kind:
            continue
        items.append(_manifest_item(message, media_kind))

    return {
        "dialog": {"id": entity.id, "name": _dialog_name(entity, source)},
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
