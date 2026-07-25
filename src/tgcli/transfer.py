"""Shared striped download and parallel part upload (ADR-0043 / ADR-0047)."""

from __future__ import annotations

import asyncio
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
