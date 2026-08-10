"""Shared striped download and parallel part upload (ADR-0043 / ADR-0047)."""

from __future__ import annotations

import asyncio
import contextlib
import hashlib
import os
from collections.abc import Awaitable, Callable, Coroutine, Iterable
from pathlib import Path
from typing import Any

from telethon import helpers, utils
from telethon.tl import functions, types

CHUNK_SIZE = 512 * 1024
CLONE_TRANSFER_PARALLEL = 4
# One chunk cadence for every transfer that reports progress (ADR-0043/0049):
# `media download`, the striped download, and the clone reupload legs.
PROGRESS_EVERY_CHUNKS = 16

# Zero-arg thunk → fresh awaitable. A coroutine object is single-use; callers
# that may retry (ADR-0052) must rebuild it, so invoke never receives one.
Invoke = Callable[[Callable[[], Awaitable[Any]]], Awaitable[Any]]


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
        # Surface the first worker cause so FloodWait keeps the exit-5 path.
        raise eg.exceptions[0]


async def download_striped(
    tg,
    media,
    destination: Path,
    *,
    size: int,
    parallel: int,
    progress: Callable[[int, int], None] | None = None,
) -> Path:
    """Write ``media`` into ``destination`` with stride-based parallel GetFile.

    ``parallel`` workers each iterate ``iter_download`` with a shared stride.
    A failure cancels siblings and deletes the partial file.
    """
    if parallel < 1:
        raise ValueError("parallel must be positive")
    if not isinstance(size, int) or size <= 0:
        raise ValueError("size must be a positive int")

    media = _photo_with_largest_size_last(media)

    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("xb") as handle:
        handle.truncate(size)
    descriptor = os.open(destination, os.O_WRONLY)
    downloaded = 0
    chunks_since_progress = 0
    chunk_count = max(1, (size + CHUNK_SIZE - 1) // CHUNK_SIZE)
    worker_count = min(parallel, chunk_count)

    async def worker(index: int) -> None:
        nonlocal downloaded, chunks_since_progress
        offset = index * CHUNK_SIZE
        async for chunk in tg.iter_download(
            media,
            offset=offset,
            stride=worker_count * CHUNK_SIZE,
            request_size=CHUNK_SIZE,
        ):
            data = bytes(chunk)
            os.pwrite(descriptor, data, offset)
            offset += worker_count * CHUNK_SIZE
            downloaded += len(data)
            chunks_since_progress += 1
            if progress is not None and chunks_since_progress >= PROGRESS_EVERY_CHUNKS:
                progress(downloaded, size)
                chunks_since_progress = 0

    try:
        await _run_workers(worker(index) for index in range(worker_count))
    except BaseException:
        destination.unlink(missing_ok=True)
        raise
    finally:
        os.close(descriptor)

    if progress is not None and chunks_since_progress:
        progress(downloaded, size)
    return destination


# One checkpoint per this many chunks. Frequent enough that a killed run
# loses seconds of transfer, rare enough that the sidecar write is noise
# against the bytes (the same cadence `media download` has always used).
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


async def download_resumable(
    tg,
    media,
    part: Path,
    *,
    offset: int,
    size: int | None,
    checkpoint: Callable[[int], None],
    progress: Callable[[int, int | None], None] | None = None,
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
    """
    part.parent.mkdir(parents=True, exist_ok=True)
    with part.open("ab" if offset else "wb") as handle:
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
        except BaseException:
            # Bookkeeping must never replace the exception being unwound: a
            # FloodWait that leaves as an OSError loses its `retry_after`,
            # and with it the exit-5 contract ADR-0072 exists to keep
            # (review finding). Losing the last few chunks is the cheaper
            # failure — the next run re-fetches them.
            with contextlib.suppress(Exception):
                _record(handle, checkpoint, current)
            raise
        _record(handle, checkpoint, current)
        if progress is not None and chunks_since_progress:
            progress(current, size)
    return current


async def upload_parts(
    tg,
    path: Path | str,
    *,
    parallel: int = CLONE_TRANSFER_PARALLEL,
    file_name: str | None = None,
    invoke: Invoke | None = None,
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

    async def run(make_awaitable):
        if invoke is None:
            return await make_awaitable()
        return await invoke(make_awaitable)

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
                result = await run(lambda: tg(request))
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
