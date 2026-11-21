"""Unit tests for the shared striped download / parallel upload seam."""

from __future__ import annotations

import asyncio

import pytest
from telethon import errors as telethon_errors
from telethon.tl import functions, types

from tgcli import transfer
from tgcli.commands import media


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


def test_media_download_shares_the_resumable_transfer_seam():
    """T31: media delegates progress cadence with the entire serial loop."""
    assert media.download_resumable is transfer.download_resumable


def _photo_media(*, sizes):
    return types.MessageMediaPhoto(
        photo=types.Photo(
            id=1,
            access_hash=2,
            file_reference=b"ref",
            date=None,
            sizes=list(sizes),
            dc_id=2,
        )
    )


@pytest.mark.asyncio
async def test_windowed_download_selects_largest_photo_size_not_list_order(tmp_path):
    """Telethon's _get_file_info trusts sizes[-1]; we reorder first."""
    from telethon import utils as telethon_utils

    large = types.PhotoSize(type="x", w=1024, h=1024, size=50_000)
    small = types.PhotoSize(type="m", w=256, h=256, size=5_000)
    media = _photo_media(sizes=[large, small])
    tg = FakeStrideTelegram()

    await transfer.download_resumable_windowed(
        tg,
        media,
        tmp_path / "out.part",
        offset=0,
        size=transfer.CHUNK_SIZE,
        parallel=1,
        checkpoint=lambda current: None,
    )

    passed = tg.calls[0]["media"]
    info = telethon_utils._get_file_info(passed)
    assert info.location.thumb_size == "x"
    assert info.size == 50_000
    assert media.photo.sizes[-1].type == "m"


@pytest.mark.asyncio
async def test_windowed_download_already_sorted_photo_sizes_unchanged(tmp_path):
    from telethon import utils as telethon_utils

    small = types.PhotoSize(type="m", w=256, h=256, size=5_000)
    large = types.PhotoSize(type="x", w=1024, h=1024, size=50_000)
    media = _photo_media(sizes=[small, large])
    tg = FakeStrideTelegram()

    await transfer.download_resumable_windowed(
        tg,
        media,
        tmp_path / "out.part",
        offset=0,
        size=transfer.CHUNK_SIZE,
        parallel=1,
        checkpoint=lambda current: None,
    )

    passed = tg.calls[0]["media"]
    info = telethon_utils._get_file_info(passed)
    assert info.location.thumb_size == "x"
    assert info.size == 50_000
    assert [s.type for s in passed.photo.sizes] == ["m", "x"]


@pytest.mark.asyncio
async def test_windowed_download_document_path_unaffected(tmp_path):
    document = types.Document(
        id=9,
        access_hash=8,
        file_reference=b"ref",
        date=None,
        mime_type="application/pdf",
        size=transfer.CHUNK_SIZE,
        dc_id=2,
        attributes=[],
    )
    media = types.MessageMediaDocument(document=document)
    tg = FakeStrideTelegram()

    await transfer.download_resumable_windowed(
        tg,
        media,
        tmp_path / "out.part",
        offset=0,
        size=transfer.CHUNK_SIZE,
        parallel=1,
        checkpoint=lambda current: None,
    )

    assert tg.calls[0]["media"] is media


class FakeSerialTelegram:
    """Streams `size` bytes from the requested offset, optionally flooding."""

    def __init__(self, size: int, *, flood_after: int | None = None):
        self.size = size
        self.flood_after = flood_after

    async def iter_download(self, media, *, offset=0, request_size=None, **kwargs):
        position = offset
        while position < self.size:
            if self.flood_after is not None and position >= self.flood_after:
                raise telethon_errors.FloodWaitError(request=None, capture=7)
            step = min(request_size or transfer.CHUNK_SIZE, self.size - position)
            position += step
            yield b"N" * step


@pytest.mark.asyncio
async def test_a_failing_checkpoint_never_replaces_the_flood_it_was_recording(
    tmp_path,
):
    """A FloodWait that unwinds into an OSError loses its retry_after.

    The whole contract is exit 5 plus a server-issued wait; bookkeeping
    on the failure path must never be able to take that away (review finding).
    """
    part = tmp_path / "src-1.part"

    def exploding_checkpoint(current: int) -> None:
        raise OSError("no space left on device")

    with pytest.raises(telethon_errors.FloodWaitError) as caught:
        await transfer.download_resumable(
            FakeSerialTelegram(
                8 * transfer.CHUNK_SIZE, flood_after=transfer.CHUNK_SIZE
            ),
            object(),
            part,
            offset=0,
            size=8 * transfer.CHUNK_SIZE,
            checkpoint=exploding_checkpoint,
        )

    assert caught.value.seconds == 7
