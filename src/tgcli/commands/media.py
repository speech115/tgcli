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
from tgcli.commands.read import _media_kind
from tgcli.errors import (
    NotFoundError,
    PartialFailure,
    PolicyError,
    RateLimitError,
    TgcliError,
)
from tgcli.output import note
from tgcli.session import state_dir
from tgcli.transfer import (
    download_resumable,
    download_striped,
    media_identity,
)

PRIVATE_LINK = re.compile(r"(?:https?://)?t\.me/c/(\d+)/(\d+)/?$")
PUBLIC_LINK = re.compile(r"(?:https?://)?t\.me/([A-Za-z0-9_]+)/([1-9]\d*)/?$")
STORY_LINK = re.compile(r"(?:https?://)?t\.me/([A-Za-z0-9_]+)/s/([1-9]\d*)/?$")
PRIVATE_STORY_LINK = re.compile(r"(?:https?://)?t\.me/c/(\d+)/s/([1-9]\d*)/?$")

MAX_FILENAME_BYTES = 200


@dataclass(frozen=True)
class MediaSource:
    chat: str | None
    message_id: int | None
    private_channel_id: int | None
    story_id: int | None = None


@dataclass(frozen=True)
class _DownloadTarget:
    """Uniform transfer input for message media and story media (ADR-0076)."""

    media: object
    filename: str | None
    size: int | None
    codec: str | None = None


def is_story_link(source: str) -> bool:
    return bool(STORY_LINK.fullmatch(source) or PRIVATE_STORY_LINK.fullmatch(source))


def parse_source(source: str, message_id: int | None) -> MediaSource:
    private_story = PRIVATE_STORY_LINK.fullmatch(source)
    if private_story:
        if message_id is not None:
            raise NotFoundError(f"invalid media source: {source!r}")
        return MediaSource(
            chat=None,
            message_id=None,
            private_channel_id=int(private_story.group(1)),
            story_id=int(private_story.group(2)),
        )

    story = STORY_LINK.fullmatch(source)
    if story:
        if message_id is not None:
            raise NotFoundError(f"invalid media source: {source!r}")
        return MediaSource(
            chat=f"@{story.group(1)}",
            message_id=None,
            private_channel_id=None,
            story_id=int(story.group(2)),
        )

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


def _video_codec(document) -> str | None:
    """Return the `documentAttributeVideo.video_codec` (h264/h265/av1)."""
    for attribute in getattr(document, "attributes", ()) or ():
        if isinstance(attribute, types.DocumentAttributeVideo):
            return attribute.video_codec
    return None


def _document_filename(document, story_id: int) -> str:
    for attribute in getattr(document, "attributes", ()) or ():
        if isinstance(attribute, types.DocumentAttributeFilename):
            return attribute.file_name
    return f"story-{story_id}.mp4"


def _story_target(story, codec: str | None) -> _DownloadTarget:
    """Pick the download target for a story (ADR-0076).

    Without a codec the main document downloads as-is. With one, the matching
    document is chosen from `document` + `alt_documents` by its
    `video_codec` attribute; `hevc` aliases Telegram's `h265`.
    """
    media = story.media
    if isinstance(media, types.MessageMediaDocument) and media.document:
        documents = [media.document] + list(media.alt_documents or [])
        wanted = "h265" if codec == "hevc" else codec
        if wanted is not None:
            for document in documents:
                if _video_codec(document) == wanted:
                    return _DownloadTarget(
                        media=document,
                        filename=_document_filename(document, story.id),
                        size=getattr(document, "size", None),
                        codec=codec,
                    )
            raise NotFoundError(f"story has no {codec} encoding")
        document = media.document
        return _DownloadTarget(
            media=media,
            filename=_document_filename(document, story.id),
            size=getattr(document, "size", None),
        )
    if codec is not None:
        raise NotFoundError(f"story has no {codec} encoding")
    if isinstance(media, types.MessageMediaPhoto):
        return _DownloadTarget(
            media=media,
            filename=f"story-{story.id}.jpg",
            size=_photo_size(media.photo),
        )
    raise NotFoundError("story has no downloadable media")


async def resolve_message(
    tg, source: MediaSource, account_alias: str, *, codec: str | None = None
) -> tuple[object, _DownloadTarget]:
    try:
        entity = (
            await _resolve_private_entity(tg, source.private_channel_id, account_alias)
            if source.private_channel_id is not None
            else await tg.get_entity(chatref.parse(source.chat))  # type: ignore  # chat set when no private link
        )
    except ValueError:
        raise NotFoundError(f"dialog not found: {source.chat!r}") from None

    if source.story_id is not None:
        stories = await tg(
            functions.stories.GetStoriesByIDRequest(
                peer=await tg.get_input_entity(entity), id=[source.story_id]
            )
        )
        if not stories.stories:
            raise NotFoundError(f"story not found: {source.story_id}")
        return entity, _story_target(stories.stories[0], codec)

    if source.message_id is None:
        raise NotFoundError(f"invalid media source: {_source_label(source)}")
    message = await tg.get_messages(entity, ids=source.message_id)
    if message is None or not getattr(message, "media", None):
        raise NotFoundError(f"downloadable media not found: {source.message_id}")
    return entity, _DownloadTarget(
        media=message.media,
        filename=_message_filename(message, source.message_id),
        size=_message_size(message),
    )


def _source_label(source: MediaSource) -> str:
    chat = (
        f"private:{source.private_channel_id}"
        if source.private_channel_id
        else source.chat
    )
    if source.story_id is not None:
        return f"story:{chat}:{source.story_id}"
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


def _photo_size(photo) -> int | None:
    """Byte count of the largest photo size, mirroring Telethon's own
    computation (utils `_get_file_info`): progressive sizes report the max
    of their steps, cached/stripped sizes are inline and have no countable
    size."""
    sizes = getattr(photo, "sizes", None)
    if not sizes:
        return None
    last = sizes[-1]
    if isinstance(last, types.PhotoSizeProgressive):
        return max(last.sizes)
    if isinstance(last, (types.PhotoCachedSize, types.PhotoStrippedSize)):
        return None
    return getattr(last, "size", None)


def _media_fingerprint(target: _DownloadTarget) -> dict:
    """What must still be true of the media for a partial file to be reused."""
    return {"media_id": media_identity(target.media), "size": target.size}


def _write_state(
    path: Path,
    source: MediaSource,
    destination: Path,
    target: _DownloadTarget,
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
                **_media_fingerprint(target),
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
    state_path: Path,
    part_path: Path,
    source: MediaSource,
    destination: Path,
    target: _DownloadTarget,
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
    # Ordering matters and is the point of this block. The state file is keyed
    # by source alone, so a re-run with a different `--output` lands on this
    # same record: that is a confused invocation and has always been exit 2.
    # The media-identity restart below is *quieter* than that error, so it is
    # asked second — otherwise a source that also replaced its media would
    # silently swallow the wrong-output diagnostic (review finding).
    if state.get("source") != _source_label(source) or state.get("destination") != str(
        destination
    ):
        raise PolicyError(
            f"media download state does not match requested output: {state_path}"
        )
    fingerprint = _media_fingerprint(target)
    if {key: state.get(key) for key in fingerprint} != fingerprint:
        # The source replaced the file behind this message. The partial bytes
        # belong to the old one, and appending the new file's tail to them
        # would publish a splice that passes every length check and is
        # neither file (#180). Not operator error — say so and start over.
        note(
            f"source media changed since the interrupted download of "
            f"{_source_label(source)}; restarting it from the beginning"
        )
        part_path.unlink(missing_ok=True)
        state_path.unlink(missing_ok=True)
        return 0
    if not part_path.exists():
        raise PolicyError(f"media download state has no partial file: {part_path}")
    offset = state.get("offset")
    part_size = part_path.stat().st_size
    if not isinstance(offset, int) or offset < 0 or offset > part_size:
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
    codec: str | None = None,
) -> dict:
    if parallel < 1:
        raise PolicyError("parallel media download count must be positive")

    _, target = await resolve_message(tg, source, account_alias, codec=codec)
    if source.story_id is not None:
        fallback_id = source.story_id
    elif source.message_id is not None:
        fallback_id = source.message_id
    else:
        raise NotFoundError(f"invalid media source: {_source_label(source)}")
    destination = destination_for(safe_filename(target.filename, fallback_id), output)
    state_path, part_path = _state_paths(source)
    offset = _resume_offset(state_path, part_path, source, destination, target)
    resumed = offset > 0

    part_path.parent.mkdir(parents=True, exist_ok=True)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if parallel > 1:
        if resumed:
            raise PolicyError(
                "parallel media download cannot resume an interrupted transfer"
            )
        _write_state(state_path, source, destination, target, 0, resumable=False)
        return await _download_parallel(
            tg,
            target,
            source,
            destination,
            state_path,
            part_path,
            parallel,
            progress,
        )

    if not resumed:
        _write_state(state_path, source, destination, target, offset)

    def checkpoint(current: int) -> None:
        _write_state(state_path, source, destination, target, current)

    current = await download_resumable(
        tg,
        target.media,
        part_path,
        offset=offset,
        size=target.size,
        checkpoint=checkpoint,
        progress=progress,
    )

    if target.size is not None and current != target.size:
        # The final name means complete; a stream that ended early is not.
        # The checkpoint above already made `current` durable, so the next
        # run resumes from here instead of restarting (ADR-0083 decision 3,
        # mirrored from clone reupload's `download_resumable` guard).
        raise PolicyError(
            f"media download stopped at {current}/{target.size} bytes "
            f"for {_source_label(source)}"
        )

    _publish(part_path, destination)
    state_path.unlink(missing_ok=True)
    data = {
        "source": _source_label(source),
        "path": str(destination),
        "bytes": destination.stat().st_size,
        "resumed": resumed,
        "parallel": 1,
    }
    if target.codec is not None:
        data["codec"] = target.codec
    return data


async def _download_parallel(
    tg,
    target: _DownloadTarget,
    source: MediaSource,
    destination: Path,
    state_path: Path,
    part_path: Path,
    parallel: int,
    progress,
) -> dict:
    total = target.size
    if not isinstance(total, int) or total <= 0:
        raise NotFoundError(
            f"media size is unavailable for parallel download: {_source_label(source)}"
        )

    try:
        await download_striped(
            tg,
            target.media,
            part_path,
            size=total,
            parallel=parallel,
            progress=progress,
        )
    except RuntimeError as exc:
        raise PolicyError(str(exc)) from exc
    _publish(part_path, destination)
    state_path.unlink(missing_ok=True)
    data = {
        "source": _source_label(source),
        "path": str(destination),
        "bytes": destination.stat().st_size,
        "resumed": False,
        "parallel": parallel,
    }
    if target.codec is not None:
        data["codec"] = target.codec
    return data


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
        try:
            result = await download_media(
                tg,
                source,
                account_alias,
                output=str(output_dir / safe_filename(target.filename, fallback_id)),
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
    """Yield ``(source, target, resolve_error)`` rows for bulk download.

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
                    target = _DownloadTarget(
                        media=message.media,
                        filename=_message_filename(message, message_id),
                        size=_message_size(message),
                    )
                else:
                    _, target = await resolve_message(tg, source, account_alias)
            except NotFoundError as exc:
                yield source, None, exc
                continue
            yield source, target, None
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
            _DownloadTarget(
                media=message.media,
                filename=_message_filename(message, message.id),
                size=_message_size(message),
            ),
            None,
        )


def bulk_to_rows(data: dict) -> list[tuple]:
    """One frozen `media download` row per downloaded item (CONTRACT §5 TSV)."""
    return [(item["path"], item["bytes"], item["resumed"], 1) for item in data["items"]]


MEDIA_KINDS = ("photo", "video", "video_note", "audio", "voice", "document")


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
