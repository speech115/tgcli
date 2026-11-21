"""Persistent clone reupload media cache."""

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from telethon import errors as telethon_errors
from telethon.tl import functions, types

from tests.conftest import make_session_fake
from tests.fakes import messages
from tests.fakes.clone import (
    SAMPLE,
    CloneReuploadClient,
    message,
    seed_clone,
)
from tgcli.cli import main
from tgcli.clone import reupload, state
from tgcli.errors import PolicyError
from tgcli.transfer import CHUNK_SIZE


@pytest.fixture
def config_env(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE)
    monkeypatch.setenv("TGCLI_CONFIG", str(path))


def _photo_message(message_id=2, size=100):
    photo = types.Photo(
        id=7,
        access_hash=1,
        file_reference=b"",
        date=None,
        sizes=[types.PhotoSize(type="x", w=1, h=1, size=size)],
        dc_id=2,
    )
    return message(
        message_id, message="caption", media=types.MessageMediaPhoto(photo=photo)
    )


@pytest.mark.asyncio
async def test_download_reuses_matching_name_and_size(state_dir_env, monkeypatch):
    clone_state = state.CloneState.new(
        account_user_id=1, source_peer_id=2, source_title="S"
    )
    clone_state.destination_peer_id = 9
    state.save(clone_state)
    cache = reupload.cache_dir(clone_state)
    cache.mkdir(parents=True)
    target = cache / "src-2"
    payload = b"cached-bytes-here"
    target.write_bytes(payload)
    reupload.complete_marker(target).touch()
    msg = _photo_message(2, size=len(payload))

    class NoDownloadTg:
        async def download_media(self, message, file=None):
            raise AssertionError("matching cache must not download")

    path = await reupload.download_for_reupload(NoDownloadTg(), msg, cache)
    assert path == target
    assert path.read_bytes() == payload


def _striped_clone_state():
    clone_state = state.CloneState.new(
        account_user_id=1, source_peer_id=2, source_title="S"
    )
    clone_state.destination_peer_id = 9
    state.save(clone_state)
    return clone_state


def test_successful_reupload_removes_media_cache(config_env, monkeypatch, capsys):
    clone_state = seed_clone()
    photo = types.MessageMediaPhoto(photo=types.PhotoEmpty(id=7))
    client = CloneReuploadClient(
        [message(2, message="caption", media=photo)], protected=True
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0
    capsys.readouterr()

    cache = state.clones_dir() / f"{clone_state.clone_id}-media"
    assert not cache.exists()


@pytest.mark.asyncio
async def test_resumed_download_reports_absolute_progress(state_dir_env, monkeypatch):
    """The percentage must describe the file, not this run's slice of it."""
    from tgcli import transfer

    monkeypatch.setattr(transfer, "PROGRESS_EVERY_CHUNKS", 1)
    clone_state = _striped_clone_state()
    cache = reupload.cache_dir(clone_state)
    cache.mkdir(parents=True)
    size = 4 * CHUNK_SIZE
    msg = _photo_message(2, size=size)
    seen: list[tuple[int, int]] = []

    class Tg:
        def __init__(self):
            self.flooded = False

        async def download_media(self, message, file=None):
            raise AssertionError("large media must stream")

        async def iter_download(self, media, *, offset=0, request_size=None, **kw):
            position = offset
            while position < size:
                if not self.flooded and position >= 2 * CHUNK_SIZE:
                    self.flooded = True
                    raise telethon_errors.FloodWaitError(request=None, capture=1)
                chunk = b"N" * min(request_size or CHUNK_SIZE, size - position)
                position += kw.get("stride") or len(chunk)
                yield chunk

    class Progress:
        def transfer(self, filename, direction):
            return lambda current, total: seen.append((current, total))

    tg = Tg()
    with pytest.raises(telethon_errors.FloodWaitError):
        await reupload.download_for_reupload(tg, msg, cache, progress=Progress())
    await reupload.download_for_reupload(tg, msg, cache, progress=Progress())

    assert seen == sorted(seen)
    assert sorted(set(seen)) == [
        (CHUNK_SIZE, size),
        (2 * CHUNK_SIZE, size),
        (3 * CHUNK_SIZE, size),
        (size, size),
    ]


@pytest.mark.asyncio
async def test_short_stream_never_publishes_the_final_name(state_dir_env):
    """The final name means complete; a stream that ends early is not.

    The striped path pre-allocated the file, so a truncated download left a
    full-size *sparse* file the reuse check would have uploaded as media.
    """
    clone_state = _striped_clone_state()
    cache = reupload.cache_dir(clone_state)
    cache.mkdir(parents=True)
    size = 4 * CHUNK_SIZE
    msg = _photo_message(2, size=size)

    class ShortTg:
        async def download_media(self, message, file=None):
            raise AssertionError("large media must stream")

        async def iter_download(self, media, *, offset=0, request_size=None, **kw):
            if offset == 0:
                yield b"N" * CHUNK_SIZE

    with pytest.raises(PolicyError, match="stopped at"):
        await reupload.download_for_reupload(ShortTg(), msg, cache)

    assert not (cache / "src-2").exists()
    assert (cache / "src-2.part").stat().st_size == CHUNK_SIZE


def _sized_video(message_id, *, size, document_id):
    document = messages.document(id=document_id, size=size, mime_type="video/mp4")
    return message(
        message_id, message="clip", media=types.MessageMediaDocument(document=document)
    )


@pytest.mark.asyncio
async def test_partial_download_of_the_same_media_still_resumes(state_dir_env):
    clone_state = _striped_clone_state()
    cache = reupload.cache_dir(clone_state)
    cache.mkdir(parents=True)
    size = 4 * CHUNK_SIZE
    part = cache / "src-2.part"
    part.write_bytes(b"O" * (2 * CHUNK_SIZE))
    reupload.download_checkpoint(part).write_text(
        json.dumps({"size": size, "media_id": 222, "offset": 2 * CHUNK_SIZE})
    )
    offsets: list[int] = []

    class Tg:
        async def download_media(self, message, file=None):
            raise AssertionError("large media must stream")

        async def iter_download(self, media, *, offset=0, request_size=None, **kw):
            offsets.append(offset)
            position = offset
            while position < size:
                chunk = b"N" * min(request_size or CHUNK_SIZE, size - position)
                position += kw.get("stride") or len(chunk)
                yield chunk

    await reupload.download_for_reupload(
        Tg(), _sized_video(2, size=size, document_id=222), cache
    )

    assert min(offsets) == 2 * CHUNK_SIZE


class StreamingReuploadClient(CloneReuploadClient):
    async def download_media(self, message, file=None):
        raise AssertionError("media over one chunk must stream")

    async def iter_download(self, media, *, offset=0, request_size=None, **kw):
        size = CHUNK_SIZE + 1
        position = offset
        while position < size:
            chunk = b"P" * min(request_size or size, size - position)
            position += len(chunk)
            yield chunk


def test_streamed_photo_uploads_under_an_image_name(config_env, monkeypatch, capsys):
    """A photo over one chunk streams to an extensionless cache file; its
    upload must still carry an image extension, or Telegram answers
    PHOTO_EXT_INVALID on every run and the clone never advances."""
    seed_clone()
    client = StreamingReuploadClient(
        [_photo_message(2, size=CHUNK_SIZE + 1)], protected=True
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0
    capsys.readouterr()

    (sent,) = [
        request
        for request in client.requests
        if isinstance(request, functions.messages.SendMediaRequest)
    ]
    assert isinstance(sent.media, types.InputMediaUploadedPhoto)
    assert sent.media.file.name == "src-2.jpg"


def test_a_small_download_under_telethons_name_is_reused_after_a_failed_run(
    config_env, monkeypatch, capsys
):
    """Telethon names a one-chunk download itself (`src-2` becomes
    `src-2.jpg`), and never overwrites: a second download of the same photo
    lands at `src-2 (1).jpg`. The cache must remember that name, or every
    failed run downloads the photo again and leaves one more copy behind."""
    clone_state = seed_clone()

    class TelethonNaming(CloneReuploadClient):
        flood = True

        async def download_media(self, message, file=None):
            path = Path(f"{file}.jpg")
            copies = 0
            while path.exists():
                copies += 1
                path = Path(f"{file} ({copies}).jpg")
            path.write_bytes(b"x" * 100)
            self.downloads.append(path)
            return str(path)

        async def __call__(self, request):
            if self.flood and isinstance(request, functions.upload.SaveFilePartRequest):
                raise telethon_errors.FloodWaitError(request=request, capture=90)
            return await super().__call__(request)

    client = TelethonNaming([_photo_message(2, size=100)], protected=True)
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 5
    client.flood = False
    after_the_wait = datetime.now(UTC) + timedelta(seconds=91)
    monkeypatch.setattr(state, "_now", lambda: after_the_wait)
    assert main(["clone", "sync", "@source", "--json"]) == 0
    capsys.readouterr()

    assert client.downloads == [client.downloads[0]]
    assert client.downloads[0].name == "src-2.jpg"
    assert state.load(clone_state.clone_id).dest_for(2) is not None
