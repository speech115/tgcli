"""Unit tests for the shared striped download / parallel upload seam (ADR-0047)."""

from __future__ import annotations

import asyncio
import hashlib

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


def _positional_body(size: int) -> bytes:
    """Bytes whose value depends on their own offset in the file.

    A stripe written at the wrong offset then changes content without changing
    length — the corruption an assertion on file size alone would never see.
    """
    body = bytearray()
    while len(body) < size:
        body += hashlib.sha256(f"tgcli-stripe:{len(body)}".encode()).digest()
    return bytes(body[:size])


class FakeBodyTelegram:
    """``iter_download`` over a real body, mirroring ``_DirectDownloadIter``.

    Telethon yields the chunk at ``offset``, then advances by ``stride``, and
    stops after the first chunk shorter than ``request_size`` — including the
    empty chunk a worker gets when its next stripe starts past end of file.
    """

    def __init__(self, body: bytes):
        self.body = body
        self.calls: list[dict] = []

    async def iter_download(self, media, *, offset=0, request_size=None, stride=None):
        self.calls.append(
            {"offset": offset, "request_size": request_size, "stride": stride}
        )
        position = offset
        while True:
            await asyncio.sleep(0)  # let sibling stripes interleave
            chunk = self.body[position : position + request_size]
            yield chunk
            if len(chunk) < request_size:
                return
            position += stride


_STRIPE_BODIES = {
    "whole-multiple": 4 * transfer.CHUNK_SIZE,
    "short-last-chunk": 3 * transfer.CHUNK_SIZE + 4321,
    "one-byte-tail": 5 * transfer.CHUNK_SIZE + 1,
    "below-one-chunk": transfer.CHUNK_SIZE // 3,
}


@pytest.mark.parametrize("label", sorted(_STRIPE_BODIES))
@pytest.mark.parametrize("parallel", [1, 2, 4])
@pytest.mark.asyncio
async def test_download_striped_reassembles_the_body_byte_for_byte(
    tmp_path, parallel, label
):
    """Stride/offset arithmetic must rebuild the source exactly, not just its size."""
    size = _STRIPE_BODIES[label]
    body = _positional_body(size)
    tg = FakeBodyTelegram(body)
    destination = tmp_path / f"{label}-{parallel}.bin"

    await transfer.download_striped(
        tg, media=object(), destination=destination, size=size, parallel=parallel
    )

    written = destination.read_bytes()
    assert len(written) == size
    assert written == body


@pytest.mark.asyncio
async def test_download_striped_stripes_cover_every_chunk_exactly_once(tmp_path):
    """The stripes a worker walks must tile the file: no gap, no overlap."""
    size = 5 * transfer.CHUNK_SIZE + 1  # last stripe short, two stripes past EOF
    tg = FakeBodyTelegram(_positional_body(size))

    await transfer.download_striped(
        tg,
        media=object(),
        destination=tmp_path / "out.bin",
        size=size,
        parallel=4,
    )

    assert {call["request_size"] for call in tg.calls} == {transfer.CHUNK_SIZE}
    assert {call["stride"] for call in tg.calls} == {4 * transfer.CHUNK_SIZE}
    covered = sorted(
        position
        for call in tg.calls
        for position in range(call["offset"], size, call["stride"])
    )
    assert covered == [index * transfer.CHUNK_SIZE for index in range(6)]


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


@pytest.mark.asyncio
async def test_download_striped_short_stream_never_publishes_the_full_size_file(
    tmp_path,
):
    """`truncate(size)` pre-allocates the file, so a stream that stops without
    raising (no FloodWait, no worker exception — the server just closed the
    connection early) would otherwise leave a full-size *sparse* file that
    looks complete (ADR-0083 decision 3, mirrored from clone's reupload
    download onto the shared striped seam)."""

    class ShortStreamTelegram(FakeStrideTelegram):
        async def iter_download(
            self, media, *, offset=0, request_size=None, stride=None
        ):
            self.calls.append({"offset": offset, "stride": stride})
            if offset == 0:
                yield b"A" * transfer.CHUNK_SIZE
            # The second stripe's stream ends with no bytes and no error.

    tg = ShortStreamTelegram()
    destination = tmp_path / "out.bin"

    with pytest.raises(RuntimeError, match=r"ended at .* bytes"):
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
async def test_upload_parts_flood_wait_surfaces_unwrapped(tmp_path):
    path = tmp_path / "multi.bin"
    path.write_bytes(b"z" * (512 * 1024))
    tg = FakeUploadTelegram(flood_on_part=0)

    with pytest.raises(telethon_errors.FloodWaitError) as raised:
        await transfer.upload_parts(tg, path, parallel=4)

    assert raised.value.seconds == 3
    assert any(
        isinstance(req, functions.upload.SaveFilePartRequest) and req.file_part == 0
        for req in tg.requests
    )


@pytest.mark.asyncio
async def test_download_striped_flood_wait_surfaces_unwrapped(tmp_path):
    class FloodTelegram(FakeStrideTelegram):
        async def iter_download(
            self, media, *, offset=0, request_size=None, stride=None
        ):
            self.calls.append(
                {
                    "media": media,
                    "offset": offset,
                    "request_size": request_size,
                    "stride": stride,
                }
            )
            raise telethon_errors.FloodWaitError(request=None, capture=12)
            yield  # pragma: no cover

    tg = FloodTelegram()
    destination = tmp_path / "out.bin"

    with pytest.raises(telethon_errors.FloodWaitError) as raised:
        await transfer.download_striped(
            tg,
            media=object(),
            destination=destination,
            size=2 * transfer.CHUNK_SIZE,
            parallel=2,
        )

    assert raised.value.seconds == 12
    assert not destination.exists()


@pytest.mark.asyncio
async def test_upload_parts_uses_invoke_wrapper(tmp_path):
    path = tmp_path / "one.bin"
    path.write_bytes(b"a" * 100)
    tg = FakeUploadTelegram()
    seen: list[object] = []
    thunks: list[object] = []

    async def invoke(make_awaitable):
        assert callable(make_awaitable)
        thunks.append(make_awaitable)
        result = await make_awaitable()
        seen.append(result)
        return result

    handle = await transfer.upload_parts(tg, path, parallel=4, invoke=invoke)

    assert handle.parts == 1
    assert seen == [True]
    assert len(tg.requests) == 1
    assert len(thunks) == 1


@pytest.mark.asyncio
async def test_upload_parts_invoke_thunk_is_rerunnable(tmp_path):
    """ADR-0052 task 1: invoke receives a zero-arg callable, not a spent coroutine."""
    path = tmp_path / "one.bin"
    path.write_bytes(b"a" * 100)
    tg = FakeUploadTelegram()
    captured: list[object] = []

    async def invoke(make_awaitable):
        captured.append(make_awaitable)
        first = make_awaitable()
        second = make_awaitable()
        assert first is not second
        assert hasattr(first, "__await__")
        assert hasattr(second, "__await__")
        # Consuming the first must not exhaust the second — proves a fresh
        # awaitable, not a re-wrapped single-use coroutine.
        await first
        return await second

    handle = await transfer.upload_parts(tg, path, parallel=4, invoke=invoke)

    assert handle.parts == 1
    assert len(captured) == 1
    assert len(tg.requests) == 2


@pytest.mark.asyncio
async def test_download_striped_reports_progress_on_the_shared_cadence(tmp_path):
    """ADR-0049: the striped download reports through the one shared cadence."""

    class MultiChunkTelegram(FakeStrideTelegram):
        async def iter_download(
            self, media, *, offset=0, request_size=None, stride=None
        ):
            self.calls.append({"offset": offset, "stride": stride})
            for index in range(transfer.PROGRESS_EVERY_CHUNKS + 1):
                yield b"q" * transfer.CHUNK_SIZE

    tg = MultiChunkTelegram()
    destination = tmp_path / "out.bin"
    size = (transfer.PROGRESS_EVERY_CHUNKS + 1) * transfer.CHUNK_SIZE
    seen: list[tuple[int, int]] = []

    await transfer.download_striped(
        tg,
        media=object(),
        destination=destination,
        size=size,
        parallel=1,
        progress=lambda current, total: seen.append((current, total)),
    )

    assert seen, "striped download must report progress"
    assert {total for _, total in seen} == {size}
    assert seen[0][0] == transfer.PROGRESS_EVERY_CHUNKS * transfer.CHUNK_SIZE
    assert seen[-1][0] == size


@pytest.mark.asyncio
async def test_upload_parts_reports_progress_with_uploaded_and_total_bytes(tmp_path):
    """ADR-0049: the upload leg reports bytes through the same callback shape."""
    path = tmp_path / "multi.bin"
    payload = b"z" * (512 * 1024)
    path.write_bytes(payload)
    tg = FakeUploadTelegram()
    seen: list[tuple[int, int]] = []

    handle = await transfer.upload_parts(
        tg,
        path,
        parallel=1,
        progress=lambda current, total: seen.append((current, total)),
    )

    assert handle.parts > 1
    assert seen, "upload must report progress"
    assert {total for _, total in seen} == {len(payload)}
    assert seen[-1][0] == len(payload)
    assert all(current <= len(payload) for current, _ in seen)


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
async def test_download_striped_selects_largest_photo_size_not_list_order(tmp_path):
    """ADR-0055: Telethon's _get_file_info trusts sizes[-1]; we reorder first."""
    from telethon import utils as telethon_utils

    large = types.PhotoSize(type="x", w=1024, h=1024, size=50_000)
    small = types.PhotoSize(type="m", w=256, h=256, size=5_000)
    media = _photo_media(sizes=[large, small])  # largest first — adversarial
    tg = FakeStrideTelegram()
    destination = tmp_path / "out.bin"

    await transfer.download_striped(
        tg,
        media=media,
        destination=destination,
        size=transfer.CHUNK_SIZE,
        parallel=1,
    )

    passed = tg.calls[0]["media"]
    info = telethon_utils._get_file_info(passed)
    assert info.location.thumb_size == "x"
    assert info.size == 50_000
    # Original media must stay untouched for callers that still hold it.
    assert media.photo.sizes[-1].type == "m"


@pytest.mark.asyncio
async def test_download_striped_already_sorted_photo_sizes_unchanged(tmp_path):
    from telethon import utils as telethon_utils

    small = types.PhotoSize(type="m", w=256, h=256, size=5_000)
    large = types.PhotoSize(type="x", w=1024, h=1024, size=50_000)
    media = _photo_media(sizes=[small, large])
    tg = FakeStrideTelegram()

    await transfer.download_striped(
        tg,
        media=media,
        destination=tmp_path / "out.bin",
        size=transfer.CHUNK_SIZE,
        parallel=1,
    )

    passed = tg.calls[0]["media"]
    info = telethon_utils._get_file_info(passed)
    assert info.location.thumb_size == "x"
    assert info.size == 50_000
    assert [s.type for s in passed.photo.sizes] == ["m", "x"]


@pytest.mark.asyncio
async def test_download_striped_document_path_unaffected(tmp_path):
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

    await transfer.download_striped(
        tg,
        media=media,
        destination=tmp_path / "out.bin",
        size=transfer.CHUNK_SIZE,
        parallel=1,
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

    ADR-0072's whole contract is exit 5 plus a server-issued wait; bookkeeping
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


@pytest.mark.asyncio
async def test_a_failing_checkpoint_on_the_success_path_is_reported(tmp_path):
    """Silently stopping to record progress would be the #169 loop again."""
    part = tmp_path / "src-2.part"

    def exploding_checkpoint(current: int) -> None:
        raise OSError("no space left on device")

    with pytest.raises(OSError):
        await transfer.download_resumable(
            FakeSerialTelegram(transfer.CHUNK_SIZE),
            object(),
            part,
            offset=0,
            size=transfer.CHUNK_SIZE,
            checkpoint=exploding_checkpoint,
        )


@pytest.mark.asyncio
async def test_the_part_file_is_synced_before_the_checkpoint_claims_it(
    tmp_path, monkeypatch
):
    """The sidecar fsyncs itself; an unsynced tail would outlive its record."""
    import os

    order: list[str] = []
    real_fsync = os.fsync
    monkeypatch.setattr(
        os, "fsync", lambda fd: (order.append("fsync"), real_fsync(fd))[1]
    )
    part = tmp_path / "src-3.part"

    await transfer.download_resumable(
        FakeSerialTelegram(transfer.CHUNK_SIZE),
        object(),
        part,
        offset=0,
        size=transfer.CHUNK_SIZE,
        checkpoint=lambda current: order.append(f"checkpoint:{current}"),
    )

    assert order[0] == "fsync"
    assert order[1] == f"checkpoint:{transfer.CHUNK_SIZE}"
