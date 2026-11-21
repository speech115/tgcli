"""Shared resumable download and parallel part upload."""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import hashlib
import os
from collections.abc import Callable, Coroutine, Iterable, Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

from telethon import helpers, utils
from telethon.tl import functions, types

from tgcli.errors import ConfigError
from tgcli.state_paths import ensure_parent_dir, restrict_file

CHUNK_SIZE = 512 * 1024
CLONE_TRANSFER_PARALLEL = 4
PROGRESS_EVERY_CHUNKS = 16


def _photo_with_largest_size_last(media):
    """Return media whose Photo.sizes ends with the true largest size.

    Telethon's ``utils._get_file_info`` (used by ``iter_download``) takes
    ``sizes[-1]`` as-is. ``media_byte_size`` / ``File.size`` already pick the
    max by ``_photo_size_byte_count``. Reorder a shallow Photo copy so the two
    agree, without hand-building an ``InputPhotoFileLocation`` (which would
    drop ``dc_id`` unless threaded separately). Documents are returned as-is.
    """
    photo = None
    if isinstance(media, types.MessageMediaPhoto):
        photo = media.photo
    elif isinstance(media, types.Photo):
        photo = media
    if not isinstance(photo, types.Photo) or len(photo.sizes) <= 1:
        return media
    largest = max(
        photo.sizes,
        key=lambda size: utils._photo_size_byte_count(size) or 0,
    )
    if photo.sizes[-1] is largest:
        return media
    reordered = [size for size in photo.sizes if size is not largest]
    reordered.append(largest)
    new_photo = types.Photo(
        id=photo.id,
        access_hash=photo.access_hash,
        file_reference=photo.file_reference,
        date=photo.date,
        sizes=reordered,
        dc_id=photo.dc_id,
        has_stickers=photo.has_stickers or None,
        video_sizes=list(photo.video_sizes) if photo.video_sizes else None,
    )
    if isinstance(media, types.MessageMediaPhoto):
        return types.MessageMediaPhoto(
            spoiler=media.spoiler or None,
            live_photo=getattr(media, "live_photo", None) or None,
            photo=new_photo,
            ttl_seconds=media.ttl_seconds,
            video=getattr(media, "video", None),
        )
    return new_photo


async def _run_workers(coros: Iterable[Coroutine[Any, Any, None]]) -> None:
    try:
        async with asyncio.TaskGroup() as group:
            for coro in coros:
                group.create_task(coro)
    except ExceptionGroup as eg:
        raise eg.exceptions[0] from eg


CHECKPOINT_EVERY_CHUNKS = 8


def _record(handle, checkpoint: Callable[[int], None], current: int) -> None:
    """Make ``current`` durable in the part file, then record it.

    The fsync comes *before* the checkpoint and is not optional: the sidecar
    is written through ``atomic.replace_text``, which fsyncs itself, so a
    flushed-but-unsynced tail would let a power loss leave a record claiming
    bytes the file does not have — and the next run would resume onto a hole
    (review finding).
    """
    handle.flush()
    os.fsync(handle.fileno())
    checkpoint(current)


class DownloadBusyError(ConfigError):
    """Another tg process is downloading this same source right now."""


@contextmanager
def checkpoint_lock(state_path: Path, label: str) -> Iterator[None]:
    """Hold the `.lock` beside a download checkpoint for the whole transfer.

    `download_resumable` appends to the shared `.part` file, so two runs
    resuming one source would interleave their chunks, record an offset past
    the media size, and wedge every later run at that offset. Never waits:
    the other run may be a long transfer. The `.lock` file holds no state.
    """
    ensure_parent_dir(state_path)
    lock = state_path.with_suffix(".lock")
    with os.fdopen(os.open(lock, os.O_WRONLY | os.O_CREAT, 0o600), "w") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            raise DownloadBusyError(
                f"media download of {label} is already running in another "
                "tg process; retry when it finishes"
            ) from None
        yield


def keep_proven_bytes(part: Path, offset: object) -> int | None:
    """Truncate ``part`` to the checkpointed ``offset`` and return it.

    None when the checkpoint cannot describe ``part``: no partial file, or an
    offset that is not a byte count within it. Bytes past a valid offset were
    written after the last checkpoint and are not proven, so they are cut.
    """
    on_disk = part.stat().st_size if part.is_file() else None
    if on_disk is None or type(offset) is not int or not 0 <= offset <= on_disk:
        return None
    if offset < on_disk:
        with part.open("r+b") as handle:
            handle.truncate(offset)
    return offset


async def download_resumable(
    tg,
    media,
    part: Path,
    *,
    offset: int,
    size: int | None,
    checkpoint: Callable[[int], None],
    progress: Callable[[int, int | None], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> int:
    """Append ``media`` into ``part`` from ``offset``; return the bytes on disk.

    Serial and resumable, the opposite trade from :func:`download_striped`:
    one request at a time, but every checkpointed byte survives the run. A
    parallel transfer writes its stripes at scattered offsets, so no byte
    count describes what it already has — which is why a transfer that must
    make progress *across* runs cannot be striped.

    ``checkpoint`` is called with the absolute byte count every
    ``CHECKPOINT_EVERY_CHUNKS`` chunks and once more on the way out, whether
    the transfer finished or raised. The caller owns what that record looks
    like and what makes it valid to resume from; this owns only the loop.
    ``should_stop``, checked after every chunk, ends the loop early; the
    bytes so far are checkpointed like any other exit.
    """
    ensure_parent_dir(part)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if offset else os.O_TRUNC)
    descriptor = os.open(part, flags, 0o600)
    restrict_file(part)
    with os.fdopen(descriptor, "ab" if offset else "wb") as handle:
        current = offset
        chunks_since_checkpoint = 0
        chunks_since_progress = 0
        try:
            async for chunk in tg.iter_download(
                media, offset=offset, request_size=CHUNK_SIZE
            ):
                handle.write(bytes(chunk))
                current = handle.tell()
                chunks_since_checkpoint += 1
                chunks_since_progress += 1
                if chunks_since_checkpoint >= CHECKPOINT_EVERY_CHUNKS:
                    _record(handle, checkpoint, current)
                    chunks_since_checkpoint = 0
                if progress is not None and chunks_since_progress >= (
                    PROGRESS_EVERY_CHUNKS
                ):
                    progress(current, size)
                    chunks_since_progress = 0
                if should_stop is not None and should_stop():
                    break
        except BaseException:
            with contextlib.suppress(Exception):
                _record(handle, checkpoint, current)
            raise
        _record(handle, checkpoint, current)
        if progress is not None and chunks_since_progress:
            progress(current, size)
    return current


class _StopRequested(Exception):
    """A worker saw ``should_stop``; the windowed download ends cleanly."""


async def download_resumable_windowed(
    tg,
    media,
    part: Path,
    *,
    offset: int,
    size: int,
    parallel: int,
    checkpoint: Callable[[int], None],
    progress: Callable[[int, int | None], None] | None = None,
    should_stop: Callable[[], bool] | None = None,
) -> int:
    """:func:`download_resumable` with ``parallel`` chunk requests in flight.

    Workers stride over the chunks after ``offset`` on one exported sender
    (Telethon borrows a single connection per DC) and write each chunk at its
    own offset. Only the contiguous prefix of finished chunks is ever
    checkpointed, so the resume contract is the serial one: the next run
    truncates the part file to the checkpoint and fetches the rest again. A
    flood or any other error on one worker cancels the others, records the
    prefix and re-raises, so every stream pauses together.
    """
    if offset % CHUNK_SIZE:
        return await download_resumable(
            tg,
            media,
            part,
            offset=offset,
            size=size,
            checkpoint=checkpoint,
            progress=progress,
            should_stop=should_stop,
        )
    media = _photo_with_largest_size_last(media)
    ensure_parent_dir(part)
    descriptor = os.open(part, os.O_WRONLY | os.O_CREAT, 0o600)
    restrict_file(part)
    os.ftruncate(descriptor, offset)
    remaining_chunks = max(1, (size - offset + CHUNK_SIZE - 1) // CHUNK_SIZE)
    workers = max(1, min(parallel, remaining_chunks))
    stride = workers * CHUNK_SIZE
    finished: dict[int, int] = {}
    prefix = offset
    chunks_since_checkpoint = 0
    chunks_since_progress = 0

    def record() -> None:
        os.fsync(descriptor)
        checkpoint(prefix)

    async def worker(index: int) -> None:
        nonlocal prefix, chunks_since_checkpoint, chunks_since_progress
        position = offset + index * CHUNK_SIZE
        if position >= size:
            return
        async for chunk in tg.iter_download(
            media, offset=position, stride=stride, request_size=CHUNK_SIZE
        ):
            data = bytes(chunk)
            if not data:
                return
            os.pwrite(descriptor, data, position)
            finished[position] = len(data)
            while prefix in finished:
                prefix += finished.pop(prefix)
            chunks_since_checkpoint += 1
            chunks_since_progress += 1
            if chunks_since_checkpoint >= CHECKPOINT_EVERY_CHUNKS:
                record()
                chunks_since_checkpoint = 0
            if progress is not None and chunks_since_progress >= PROGRESS_EVERY_CHUNKS:
                progress(prefix, size)
                chunks_since_progress = 0
            if should_stop is not None and should_stop():
                raise _StopRequested
            position += stride
            if position >= size or len(data) < CHUNK_SIZE:
                return

    try:
        try:
            await _run_workers(worker(index) for index in range(workers))
        except _StopRequested:
            pass
    except BaseException:
        with contextlib.suppress(Exception):
            record()
        raise
    finally:
        with contextlib.suppress(Exception):
            os.ftruncate(descriptor, prefix)
        os.close(descriptor)
    with open(part, "rb+") as handle:
        _record(handle, checkpoint, prefix)
    if progress is not None and chunks_since_progress:
        progress(prefix, size)
    return prefix


async def upload_parts(
    tg,
    path: Path | str,
    *,
    parallel: int = CLONE_TRANSFER_PARALLEL,
    file_name: str | None = None,
    progress: Callable[[int, int], None] | None = None,
) -> types.TypeInputFile:
    """Upload ``path`` with up to ``parallel`` concurrent Save*FilePart RPCs.

    Small single-part files still go through this path (one request). FloodWait
    or any other failure cancels sibling workers.
    """
    if parallel < 1:
        raise ValueError("parallel must be positive")
    path = Path(path)
    file_size = path.stat().st_size
    part_size = int(utils.get_appropriated_part_size(file_size) * 1024)
    file_id = helpers.generate_random_long()
    name = file_name or path.name or str(file_id)
    is_big = file_size > 10 * 1024 * 1024
    part_count = max(1, (file_size + part_size - 1) // part_size)
    hash_md5 = hashlib.md5()
    if not is_big:
        hash_md5.update(path.read_bytes())

    uploaded = 0
    parts_since_progress = 0

    async def worker(index: int) -> None:
        nonlocal uploaded, parts_since_progress
        with path.open("rb") as handle:
            for part_index in range(index, part_count, parallel):
                handle.seek(part_index * part_size)
                part = handle.read(part_size)
                if is_big:
                    request: object = functions.upload.SaveBigFilePartRequest(
                        file_id, part_index, part_count, part
                    )
                else:
                    request = functions.upload.SaveFilePartRequest(
                        file_id, part_index, part
                    )
                result = await tg(request)
                if not result:
                    raise RuntimeError(f"Failed to upload file part {part_index}")
                uploaded += len(part)
                parts_since_progress += 1
                if (
                    progress is not None
                    and parts_since_progress >= PROGRESS_EVERY_CHUNKS
                ):
                    progress(uploaded, file_size)
                    parts_since_progress = 0

    worker_count = min(parallel, part_count)
    await _run_workers(worker(index) for index in range(worker_count))
    if progress is not None and parts_since_progress:
        progress(uploaded, file_size)

    if is_big:
        return types.InputFileBig(file_id, part_count, name)
    return types.InputFile(
        id=file_id,
        parts=part_count,
        name=name,
        md5_checksum=hash_md5.hexdigest(),
    )


def media_identity(media) -> int | None:
    """Telegram's own id for a media object, or None when it has none.

    Byte length does not identify media: a source can replace the file behind
    a message, and resuming a partial download onto the replacement splices
    two files into one that passes every length check and is neither
    (#169 for clone, #180 for `media download`). The document/photo id
    changes whenever the file does, so it is the identity a resume must hold.

    Accepts every shape the two callers hold: a ``MessageMedia*`` wrapper, or
    a bare ``Document`` picked out of a story's alternative encodings.

    The final fallback reads ``media.id`` off the wrapper itself, which is
    right for a bare document and wrong for any wrapper carrying an unrelated
    top-level id — ``MessageMediaStory.id`` is the *story* id, not a file's.
    That is harmless only because Telethon cannot resolve such media to a
    download location at all: ``iter_download`` raises before a byte is
    written, so no partial file exists to mis-identify. A future media type
    that is both downloadable and carries its own id would break that, and
    the fallback would need to name the types it accepts (review finding).
    """
    for candidate in (
        getattr(media, "document", None),
        getattr(media, "photo", None),
        media,
    ):
        value = getattr(candidate, "id", None)
        if type(value) is int:
            return value
    return None


def media_byte_size(message) -> int | None:
    """Best-effort media size for choosing striped download."""
    file = getattr(message, "file", None)
    size = getattr(file, "size", None)
    if isinstance(size, int) and size > 0:
        return size
    media = getattr(message, "media", None)
    document = getattr(media, "document", None)
    size = getattr(document, "size", None)
    if isinstance(size, int) and size > 0:
        return size
    return None
