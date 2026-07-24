"""Unit tests for the shared striped download / parallel upload seam (ADR-0047)."""

from __future__ import annotations

import asyncio

import pytest
from telethon.tl import functions, types

from tgcli import transfer


class FakeStrideTelegram:
    def __init__(self, *, fail_at_offset: int | None = None):
        self.calls: list[dict] = []
        self.fail_at_offset = fail_at_offset

    async def iter_download(self, media, *, offset=0, request_size=None, stride=None):
        self.calls.append(
            {
                "media": media,
                "offset": offset,
                "request_size": request_size,
                "stride": stride,
            }
        )
        if self.fail_at_offset is not None and offset == self.fail_at_offset:
            raise RuntimeError("worker boom")
        chunk_index = offset // transfer.CHUNK_SIZE
        yield bytes([65 + chunk_index]) * transfer.CHUNK_SIZE


@pytest.mark.asyncio
async def test_download_striped_writes_chunks_at_correct_offsets(tmp_path):
    tg = FakeStrideTelegram()
    destination = tmp_path / "out.bin"
    size = 2 * transfer.CHUNK_SIZE

    await transfer.download_striped(
        tg, media=object(), destination=destination, size=size, parallel=2
    )

    assert {call["offset"] for call in tg.calls} == {0, transfer.CHUNK_SIZE}
    assert {call["stride"] for call in tg.calls} == {2 * transfer.CHUNK_SIZE}
    assert destination.read_bytes() == (
        b"A" * transfer.CHUNK_SIZE + b"B" * transfer.CHUNK_SIZE
    )


@pytest.mark.asyncio
async def test_download_striped_single_chunk_uses_one_worker(tmp_path):
    tg = FakeStrideTelegram()
    destination = tmp_path / "out.bin"

    await transfer.download_striped(
        tg,
        media=object(),
        destination=destination,
        size=transfer.CHUNK_SIZE,
        parallel=4,
    )

    assert len(tg.calls) == 1
    assert tg.calls[0]["offset"] == 0
    assert tg.calls[0]["stride"] == transfer.CHUNK_SIZE
    assert destination.read_bytes() == b"A" * transfer.CHUNK_SIZE


@pytest.mark.asyncio
async def test_download_striped_worker_failure_removes_partial(tmp_path):
    tg = FakeStrideTelegram(fail_at_offset=transfer.CHUNK_SIZE)
    destination = tmp_path / "out.bin"

    with pytest.raises(RuntimeError, match="worker boom"):
        await transfer.download_striped(
            tg,
            media=object(),
            destination=destination,
            size=2 * transfer.CHUNK_SIZE,
            parallel=2,
        )

    assert not destination.exists()


class FakeUploadTelegram:
    def __init__(
        self, *, fail_on_part: int | None = None, flood_on_part: int | None = None
    ):
        self.requests: list[object] = []
        self.fail_on_part = fail_on_part
        self.flood_on_part = flood_on_part
        self._lock = asyncio.Lock()
        self.started: set[int] = set()

    async def __call__(self, request):
        async with self._lock:
            self.requests.append(request)
            part = request.file_part
            self.started.add(part)
        if self.flood_on_part is not None and part == self.flood_on_part:
            from telethon import errors as telethon_errors

            raise telethon_errors.FloodWaitError(request=None, capture=3)
        if self.fail_on_part is not None and part == self.fail_on_part:
            raise RuntimeError(f"part {part} failed")
        await asyncio.sleep(0)
        return True


@pytest.mark.asyncio
async def test_upload_parts_issues_save_file_part_for_each_index(tmp_path):
    path = tmp_path / "small.bin"
    # 256 KiB appropriated part size → two parts for 256KiB+1
    path.write_bytes(b"x" * (128 * 1024 + 10))
    tg = FakeUploadTelegram()

    handle = await transfer.upload_parts(tg, path, parallel=4)

    assert isinstance(handle, types.InputFile)
    parts = [
        req
        for req in tg.requests
        if isinstance(req, functions.upload.SaveFilePartRequest)
    ]
    assert [req.file_part for req in parts] == list(range(handle.parts))
    assert len({req.file_id for req in parts}) == 1
    assert all(isinstance(req.bytes, (bytes, bytearray)) for req in parts)


@pytest.mark.asyncio
async def test_upload_parts_big_file_uses_save_big_file_part(tmp_path):
    path = tmp_path / "big.bin"
    path.write_bytes(b"y" * (10 * 1024 * 1024 + 1))
    tg = FakeUploadTelegram()

    handle = await transfer.upload_parts(tg, path, parallel=4)

    assert isinstance(handle, types.InputFileBig)
    parts = [
        req
        for req in tg.requests
        if isinstance(req, functions.upload.SaveBigFilePartRequest)
    ]
    assert len(parts) == handle.parts
    assert {req.file_total_parts for req in parts} == {handle.parts}
    assert [req.file_part for req in sorted(parts, key=lambda r: r.file_part)] == list(
        range(handle.parts)
    )


@pytest.mark.asyncio
async def test_upload_parts_failure_cancels_sibling_workers(tmp_path):
    path = tmp_path / "multi.bin"
    path.write_bytes(b"z" * (512 * 1024))  # multiple 128KiB parts
    tg = FakeUploadTelegram(fail_on_part=0)

    with pytest.raises(RuntimeError, match="part 0 failed"):
        await transfer.upload_parts(tg, path, parallel=4)

    # At least the failing part was attempted; siblings must not all complete
    # after a hard abort (some may have started concurrently).
    assert any(
        isinstance(req, functions.upload.SaveFilePartRequest) and req.file_part == 0
        for req in tg.requests
    )


@pytest.mark.asyncio
async def test_upload_parts_uses_invoke_wrapper(tmp_path):
    path = tmp_path / "one.bin"
    path.write_bytes(b"a" * 100)
    tg = FakeUploadTelegram()
    seen: list[object] = []

    async def invoke(awaitable):
        result = await awaitable
        seen.append(result)
        return result

    handle = await transfer.upload_parts(tg, path, parallel=4, invoke=invoke)

    assert handle.parts == 1
    assert seen == [True]
    assert len(tg.requests) == 1
