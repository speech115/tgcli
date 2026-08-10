"""Persistent clone reupload media cache (ADR-0052 task 4)."""

import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from telethon import errors as telethon_errors
from telethon.tl import functions, types

from tests.conftest import make_session_fake
from tests.test_cli_clone_sync import (
    SAMPLE,
    CloneReuploadClient,
    message,
    seed_clone,
)
from tgcli.cli import main
from tgcli.clone import state
from tgcli.commands import clone as clone_cmd
from tgcli.errors import PolicyError
from tgcli.transfer import CHUNK_SIZE, media_byte_size


@pytest.fixture
def config_env(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE)
    monkeypatch.setenv("TGCLI_CONFIG", str(path))


def _photo_message(message_id=2, size=100):
    photo = types.MessageMediaPhoto(photo=types.PhotoEmpty(id=7))
    msg = message(message_id, message="caption", media=photo)
    msg.file = SimpleNamespace(size=size)
    return msg


def test_media_cache_dir_is_under_clones_state(state_dir_env):
    clone_state = state.CloneState.new(
        account_user_id=1, source_peer_id=2, source_title="S"
    )
    expected = state.clones_dir() / f"{clone_state.clone_id}-media"
    assert clone_cmd._media_cache_dir(clone_state) == expected


@pytest.mark.asyncio
async def test_download_reuses_matching_name_and_size(state_dir_env, monkeypatch):
    clone_state = state.CloneState.new(
        account_user_id=1, source_peer_id=2, source_title="S"
    )
    clone_state.destination_peer_id = 9
    state.save(clone_state)
    cache = clone_cmd._media_cache_dir(clone_state)
    cache.mkdir(parents=True)
    target = cache / "src-2"
    payload = b"cached-bytes-here"
    target.write_bytes(payload)
    clone_cmd._complete_marker(target).touch()
    msg = _photo_message(2, size=len(payload))

    class NoDownloadTg:
        async def download_media(self, message, file=None):
            raise AssertionError("matching cache must not download")

    path = await clone_cmd._download_for_reupload(
        NoDownloadTg(), msg, cache, clone_state
    )
    assert path == target
    assert path.read_bytes() == payload


@pytest.mark.asyncio
async def test_unmarked_full_size_cache_file_is_redownloaded(state_dir_env):
    """A pre-fix binary killed mid-stripe left a full-size sparse file at the
    final name; size alone must never license reuse (ADR-0052)."""
    clone_state = state.CloneState.new(
        account_user_id=1, source_peer_id=2, source_title="S"
    )
    clone_state.destination_peer_id = 9
    state.save(clone_state)
    cache = clone_cmd._media_cache_dir(clone_state)
    cache.mkdir(parents=True)
    target = cache / "src-2"
    size = 32
    with target.open("wb") as handle:
        handle.truncate(size)
    msg = _photo_message(2, size=size)
    downloads: list[Path] = []

    class Tg:
        async def download_media(self, message, file=None):
            path = Path(f"{file}")
            path.write_bytes(b"R" * size)
            downloads.append(path)
            return str(path)

    path = await clone_cmd._download_for_reupload(Tg(), msg, cache, clone_state)

    assert len(downloads) == 1
    assert path.read_bytes() == b"R" * size
    assert clone_cmd._complete_marker(path).is_file()


@pytest.mark.asyncio
async def test_download_rejects_stale_size_and_redownloads(state_dir_env):
    clone_state = state.CloneState.new(
        account_user_id=1, source_peer_id=2, source_title="S"
    )
    clone_state.destination_peer_id = 9
    state.save(clone_state)
    cache = clone_cmd._media_cache_dir(clone_state)
    cache.mkdir(parents=True)
    target = cache / "src-2"
    target.write_bytes(b"stale")
    msg = _photo_message(2, size=20)
    downloads: list[Path] = []

    class Tg:
        async def download_media(self, message, file=None):
            path = Path(f"{file}")
            path.write_bytes(b"x" * 20)
            downloads.append(path)
            return str(path)

    path = await clone_cmd._download_for_reupload(Tg(), msg, cache, clone_state)
    assert path.read_bytes() == b"x" * 20
    assert len(downloads) == 1


@pytest.mark.asyncio
async def test_stale_large_cache_redownloads_without_file_exists_error(state_dir_env):
    """Abandoned src-<id> of wrong size (>512KiB) re-downloads, no FileExistsError."""
    clone_state = state.CloneState.new(
        account_user_id=1, source_peer_id=2, source_title="S"
    )
    clone_state.destination_peer_id = 9
    state.save(clone_state)
    cache = clone_cmd._media_cache_dir(clone_state)
    cache.mkdir(parents=True)
    target = cache / "src-2"
    size = CHUNK_SIZE + 1
    target.write_bytes(b"stale-partial")
    assert target.stat().st_size != size
    msg = _photo_message(2, size=size)
    downloads: list[Path] = []

    class Tg:
        async def download_media(self, message, file=None):
            raise AssertionError("large media must stream")

        async def iter_download(self, media, *, offset=0, request_size=None, **kw):
            downloads.append(Path("streamed"))
            position = offset
            while position < size:
                chunk = b"N" * min(request_size or size, size - position)
                position += len(chunk)
                yield chunk

    path = await clone_cmd._download_for_reupload(Tg(), msg, cache, clone_state)
    assert path == target
    assert path.stat().st_size == size
    assert path.read_bytes()[:13] != b"stale-partial"
    assert downloads


def _striped_clone_state():
    clone_state = state.CloneState.new(
        account_user_id=1, source_peer_id=2, source_title="S"
    )
    clone_state.destination_peer_id = 9
    state.save(clone_state)
    return clone_state


@pytest.mark.asyncio
async def test_streamed_download_publishes_final_name_only_when_complete(state_dir_env):
    """A killed download must not leave a full-size file to be reused."""
    clone_state = _striped_clone_state()
    cache = clone_cmd._media_cache_dir(clone_state)
    cache.mkdir(parents=True)
    target = cache / "src-2"
    size = CHUNK_SIZE + 1
    msg = _photo_message(2, size=size)
    final_seen: list[bool] = []

    class Tg:
        async def download_media(self, message, file=None):
            raise AssertionError("large media must stream")

        async def iter_download(self, media, *, offset=0, request_size=None, **kw):
            final_seen.append(target.exists())
            position = offset
            while position < size:
                chunk = b"N" * min(request_size or size, size - position)
                position += len(chunk)
                yield chunk

    path = await clone_cmd._download_for_reupload(Tg(), msg, cache, clone_state)
    assert final_seen and not any(final_seen)
    assert path == target
    assert target.stat().st_size == size
    assert list(cache.glob("*.part")) == []


@pytest.mark.asyncio
async def test_interrupted_download_leaves_no_final_file(state_dir_env):
    clone_state = _striped_clone_state()
    cache = clone_cmd._media_cache_dir(clone_state)
    cache.mkdir(parents=True)
    size = CHUNK_SIZE + 1
    msg = _photo_message(2, size=size)

    class Tg:
        async def download_media(self, message, file=None):
            raise AssertionError("large media must stream")

        async def iter_download(
            self, media, *, offset=0, request_size=None, stride=None
        ):
            raise telethon_errors.RPCError(SimpleNamespace(), "BROKEN", 400)
            yield b""  # pragma: no cover - generator marker

    with pytest.raises(telethon_errors.RPCError):
        await clone_cmd._download_for_reupload(Tg(), msg, cache, clone_state)
    assert not (cache / "src-2").exists()


@pytest.mark.asyncio
async def test_abandoned_part_file_is_not_reused_and_redownloads(state_dir_env):
    """A kill mid-stripe leaves the pre-allocated .part; the next run replaces it."""
    clone_state = _striped_clone_state()
    cache = clone_cmd._media_cache_dir(clone_state)
    cache.mkdir(parents=True)
    size = CHUNK_SIZE + 1
    part = cache / "src-2.part"
    with part.open("xb") as handle:
        handle.truncate(size)
    msg = _photo_message(2, size=size)

    class Tg:
        async def download_media(self, message, file=None):
            raise AssertionError("large media must stream")

        async def iter_download(self, media, *, offset=0, request_size=None, **kw):
            position = offset
            while position < size:
                chunk = b"N" * min(request_size or size, size - position)
                position += len(chunk)
                yield chunk

    path = await clone_cmd._download_for_reupload(Tg(), msg, cache, clone_state)
    assert path == cache / "src-2"
    assert path.read_bytes()[:1] == b"N"
    assert not part.exists()


@pytest.mark.asyncio
async def test_streamed_download_result_is_reused_without_rpcs(state_dir_env):
    clone_state = _striped_clone_state()
    cache = clone_cmd._media_cache_dir(clone_state)
    cache.mkdir(parents=True)
    size = CHUNK_SIZE + 1
    msg = _photo_message(2, size=size)

    class Tg:
        async def download_media(self, message, file=None):
            raise AssertionError("large media must stream")

        async def iter_download(self, media, *, offset=0, request_size=None, **kw):
            position = offset
            while position < size:
                chunk = b"N" * min(request_size or size, size - position)
                position += len(chunk)
                yield chunk

    class NoRpcTg:
        async def download_media(self, message, file=None):
            raise AssertionError("complete cache must not download")

        async def iter_download(self, *args, **kwargs):
            raise AssertionError("complete cache must not download")
            yield b""  # pragma: no cover - generator marker

    first = await clone_cmd._download_for_reupload(Tg(), msg, cache, clone_state)
    again = await clone_cmd._download_for_reupload(NoRpcTg(), msg, cache, clone_state)
    assert again == first
    assert again.stat().st_size == size


@pytest.mark.asyncio
async def test_download_without_predictable_size_never_reuses(state_dir_env):
    clone_state = state.CloneState.new(
        account_user_id=1, source_peer_id=2, source_title="S"
    )
    clone_state.destination_peer_id = 9
    state.save(clone_state)
    cache = clone_cmd._media_cache_dir(clone_state)
    cache.mkdir(parents=True)
    target = cache / "src-2"
    target.write_bytes(b"whatever")
    msg = message(
        2,
        message="caption",
        media=types.MessageMediaPhoto(photo=types.PhotoEmpty(id=7)),
    )
    # No .file.size and no document.size → media_byte_size is None
    assert media_byte_size(msg) is None
    downloads = []

    class Tg:
        async def download_media(self, message, file=None):
            path = Path(f"{file}")
            path.write_bytes(b"fresh")
            downloads.append(path)
            return str(path)

    path = await clone_cmd._download_for_reupload(Tg(), msg, cache, clone_state)
    assert path.read_bytes() == b"fresh"
    assert len(downloads) == 1


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


def test_failed_reupload_leaves_downloaded_media_on_disk(
    config_env, monkeypatch, capsys
):
    clone_state = seed_clone()
    photo = types.MessageMediaPhoto(photo=types.PhotoEmpty(id=7))

    class FloodAfterDownload(CloneReuploadClient):
        async def download_media(self, message, file=None):
            path = Path(f"{file}")
            path.write_bytes(b"x" * 50)
            self.downloads.append(path)
            return str(path)

        async def __call__(self, request):
            if isinstance(request, functions.upload.SaveFilePartRequest):
                self.part_requests.append(request)
                raise telethon_errors.FloodWaitError(request=request, capture=90)
            return await super().__call__(request)

    client = FloodAfterDownload(
        [message(2, message="caption", media=photo)], protected=True
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 5
    capsys.readouterr()

    cache = state.clones_dir() / f"{clone_state.clone_id}-media"
    assert cache.is_dir()
    # Name the files exactly: glob("src-*") also matches the .done marker,
    # and directory order is filesystem-dependent (APFS listed the empty
    # marker first — the first macOS CI leg caught this).
    assert (cache / "src-2").read_bytes() == b"x" * 50
    assert (cache / "src-2.done").is_file()


def test_reupload_uses_persistent_cache_path_not_temp(
    config_env, monkeypatch, capsys, tmp_path
):
    """While downloading, files land under clones/<id>-media/, not a temp dir."""
    clone_state = seed_clone()
    seen: list[Path] = []
    photo = types.MessageMediaPhoto(photo=types.PhotoEmpty(id=7))

    class TrackingClient(CloneReuploadClient):
        async def download_media(self, message, file=None):
            path = Path(f"{file}")
            seen.append(path)
            path.write_bytes(b"x" * 40)
            self.downloads.append(path)
            return str(path)

    client = TrackingClient(
        [message(2, message="caption", media=photo)], protected=True
    )
    make_session_fake(monkeypatch, client)

    assert main(["clone", "sync", "@source", "--json"]) == 0
    capsys.readouterr()

    assert seen
    expected_root = state.clones_dir() / f"{clone_state.clone_id}-media"
    assert all(
        expected_root in path.parents or path.parent == expected_root for path in seen
    )
    assert all("tgcli-clone-reupload-" not in str(path) for path in seen)


@pytest.mark.asyncio
async def test_flood_mid_download_resumes_on_the_next_run(state_dir_env):
    """#169: a run that draws FloodWait keeps its bytes for the next one.

    The reupload download used to restart from byte 0 every invocation, so a
    protected file larger than one run's flood budget could never finish.
    """
    clone_state = _striped_clone_state()
    cache = clone_cmd._media_cache_dir(clone_state)
    cache.mkdir(parents=True)
    size = 4 * CHUNK_SIZE
    msg = _photo_message(2, size=size)
    offsets: list[int] = []

    class FloodOnceTg:
        def __init__(self):
            self.flooded = False

        async def download_media(self, message, file=None):
            raise AssertionError("large media must stream")

        async def iter_download(self, media, *, offset=0, request_size=None, **kw):
            offsets.append(offset)
            position = offset
            while position < size:
                if not self.flooded and position >= offset + 2 * CHUNK_SIZE:
                    self.flooded = True
                    raise telethon_errors.FloodWaitError(request=None, capture=1)
                chunk = b"N" * min(request_size or CHUNK_SIZE, size - position)
                position += len(chunk)
                yield chunk

    tg = FloodOnceTg()
    with pytest.raises(telethon_errors.FloodWaitError):
        await clone_cmd._download_for_reupload(tg, msg, cache, clone_state)

    part = cache / "src-2.part"
    assert part.stat().st_size == 2 * CHUNK_SIZE
    assert not (cache / "src-2").exists()

    path = await clone_cmd._download_for_reupload(tg, msg, cache, clone_state)

    # Second run picked up where the first stopped, not at zero.
    assert offsets == [0, 2 * CHUNK_SIZE]
    assert path == cache / "src-2"
    assert path.stat().st_size == size
    assert list(cache.glob("*.part*")) == []


@pytest.mark.asyncio
async def test_resumed_download_reports_absolute_progress(state_dir_env, monkeypatch):
    """The percentage must describe the file, not this run's slice of it."""
    from tgcli import transfer

    monkeypatch.setattr(transfer, "PROGRESS_EVERY_CHUNKS", 1)
    clone_state = _striped_clone_state()
    cache = clone_cmd._media_cache_dir(clone_state)
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
                position += len(chunk)
                yield chunk

    class Progress:
        def transfer(self, filename, direction):
            return lambda current, total: seen.append((current, total))

    tg = Tg()
    with pytest.raises(telethon_errors.FloodWaitError):
        await clone_cmd._download_for_reupload(
            tg, msg, cache, clone_state, progress=Progress()
        )
    await clone_cmd._download_for_reupload(
        tg, msg, cache, clone_state, progress=Progress()
    )

    # First run reports its two chunks, the second picks up at the third.
    assert seen == [
        (CHUNK_SIZE, size),
        (2 * CHUNK_SIZE, size),
        (3 * CHUNK_SIZE, size),
        (size, size),
    ]


@pytest.mark.asyncio
async def test_partial_download_for_a_different_size_is_discarded(state_dir_env):
    """The cached bytes belong to one media revision, not to the name."""
    clone_state = _striped_clone_state()
    cache = clone_cmd._media_cache_dir(clone_state)
    cache.mkdir(parents=True)
    part = cache / "src-2.part"
    part.write_bytes(b"O" * CHUNK_SIZE)
    clone_cmd._download_checkpoint(part).write_text(
        json.dumps({"size": 999999, "offset": CHUNK_SIZE})
    )
    size = 2 * CHUNK_SIZE
    msg = _photo_message(2, size=size)
    offsets: list[int] = []

    class Tg:
        async def download_media(self, message, file=None):
            raise AssertionError("large media must stream")

        async def iter_download(self, media, *, offset=0, request_size=None, **kw):
            offsets.append(offset)
            position = offset
            while position < size:
                chunk = b"N" * min(request_size or CHUNK_SIZE, size - position)
                position += len(chunk)
                yield chunk

    path = await clone_cmd._download_for_reupload(Tg(), msg, cache, clone_state)

    assert offsets == [0]
    assert path.read_bytes() == b"N" * size


@pytest.mark.asyncio
async def test_short_stream_never_publishes_the_final_name(state_dir_env):
    """The final name means complete; a stream that ends early is not.

    The striped path pre-allocated the file, so a truncated download left a
    full-size *sparse* file the reuse check would have uploaded as media.
    """
    clone_state = _striped_clone_state()
    cache = clone_cmd._media_cache_dir(clone_state)
    cache.mkdir(parents=True)
    size = 4 * CHUNK_SIZE
    msg = _photo_message(2, size=size)

    class ShortTg:
        async def download_media(self, message, file=None):
            raise AssertionError("large media must stream")

        async def iter_download(self, media, *, offset=0, request_size=None, **kw):
            yield b"N" * CHUNK_SIZE

    with pytest.raises(PolicyError, match="stopped at"):
        await clone_cmd._download_for_reupload(ShortTg(), msg, cache, clone_state)

    assert not (cache / "src-2").exists()
    # The bytes that did arrive are kept for the next run.
    assert (cache / "src-2.part").stat().st_size == CHUNK_SIZE


def _sized_video(message_id, *, size, document_id):
    document = SimpleNamespace(
        id=document_id, mime_type="video/mp4", attributes=[], size=size
    )
    msg = message(
        message_id, message="clip", media=types.MessageMediaDocument(document=document)
    )
    msg.file = SimpleNamespace(size=size)
    return msg


@pytest.mark.asyncio
async def test_partial_download_of_a_replaced_media_is_discarded(state_dir_env):
    """A same-size replacement must not be spliced onto the old prefix.

    Size was the only identity check, so re-exporting a video with identical
    settings produced a byte-identical length — the resume would have written
    the new tail onto the old head and reuploaded the hybrid as a faithful
    copy (review finding).
    """
    clone_state = _striped_clone_state()
    cache = clone_cmd._media_cache_dir(clone_state)
    cache.mkdir(parents=True)
    size = 4 * CHUNK_SIZE
    part = cache / "src-2.part"
    part.write_bytes(b"O" * (2 * CHUNK_SIZE))
    clone_cmd._download_checkpoint(part).write_text(
        json.dumps({"size": size, "media_id": 111, "offset": 2 * CHUNK_SIZE})
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
                position += len(chunk)
                yield chunk

    path = await clone_cmd._download_for_reupload(
        Tg(), _sized_video(2, size=size, document_id=222), cache, clone_state
    )

    assert offsets == [0]
    assert path.read_bytes() == b"N" * size


@pytest.mark.asyncio
async def test_partial_download_of_the_same_media_still_resumes(state_dir_env):
    clone_state = _striped_clone_state()
    cache = clone_cmd._media_cache_dir(clone_state)
    cache.mkdir(parents=True)
    size = 4 * CHUNK_SIZE
    part = cache / "src-2.part"
    part.write_bytes(b"O" * (2 * CHUNK_SIZE))
    clone_cmd._download_checkpoint(part).write_text(
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
                position += len(chunk)
                yield chunk

    await clone_cmd._download_for_reupload(
        Tg(), _sized_video(2, size=size, document_id=222), cache, clone_state
    )

    assert offsets == [2 * CHUNK_SIZE]
