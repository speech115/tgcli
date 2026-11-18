import errno
import json
import os
from pathlib import Path

import pytest
from telethon import errors as telethon_errors
from telethon.tl import types

from tests.conftest import ns
from tgcli.commands import media
from tgcli.commands.media import (
    MediaSource,
    _resume_offset,
    _source_label,
    _state_paths,
    download_media,
    parse_source,
    resolve_message,
    safe_filename,
)
from tgcli.errors import NotFoundError, PolicyError


def make_channel(channel_id: int) -> types.Channel:
    return types.Channel(
        id=channel_id,
        title="Chan",
        photo=None,
        date=None,
        broadcast=True,
        access_hash=0,
    )


def make_user(user_id: int) -> types.User:
    return types.User(id=user_id, first_name="Same Id", access_hash=0)


def _target(media_obj=None, size=None):
    """Minimal `_DownloadTarget` for the `_resume_offset` unit tests."""
    from tgcli.commands.media import _DownloadTarget

    return _DownloadTarget(media=media_obj or object(), filename="out.bin", size=size)


def test_parse_source_accepts_private_link():
    assert parse_source("https://t.me/c/3817664407/878", None) == MediaSource(
        chat=None, message_id=878, private_channel_id=3817664407
    )


def test_parse_source_rejects_incomplete_reference():
    with pytest.raises(NotFoundError, match="media source"):
        parse_source("@channel", None)


def test_safe_filename_cannot_escape_destination():
    assert safe_filename("../../a\tb.mp4", 42) == "a b.mp4"


class FakeDownloadTelegram:
    def __init__(self, chunks, *, fail_after_first=False):
        self.entity = type("Entity", (), {"id": 12})()
        self.message = type(
            "Message",
            (),
            {
                "id": 42,
                "media": object(),
                "file": type("File", (), {"name": "clip.bin", "size": 6})(),
            },
        )()
        self.chunks = chunks
        self.fail_after_first = fail_after_first
        self.iter_download_calls = []

    async def get_entity(self, chat):
        return self.entity

    async def get_messages(self, requested_entity, ids):
        return self.message

    async def iter_download(self, media, *, offset=0, request_size=None, **kwargs):
        self.iter_download_calls.append(
            {"media": media, "offset": offset, "request_size": request_size, **kwargs}
        )
        for index, chunk in enumerate(self.chunks):
            yield chunk
            if index == 0 and self.fail_after_first:
                raise RuntimeError("network dropped")


async def test_download_never_publishes_a_short_stream(tmp_path):
    """The final name means complete; a stream that ends early is not.

    Mirrors clone reupload's `download_resumable` guard:
    the serial loop must require `current == size` before `_publish`, not
    just when an exception unwinds the transfer.
    """
    source = MediaSource("@channel", 42, None)
    target = tmp_path / "out.bin"
    fake = FakeDownloadTelegram([b"only"])
    fake.message.file.size = 20

    with pytest.raises(PolicyError, match=r"stopped at 4/20 bytes"):
        await download_media(fake, source, "main", output=str(target))

    assert not target.exists()
    # The bytes that did arrive are kept for the next run's resume.
    _, part_path = _state_paths(source)
    assert part_path.read_bytes() == b"only"


async def test_a_failing_unwind_checkpoint_never_replaces_the_flood(
    tmp_path, monkeypatch
):
    """bookkeeping on the failure path must not erase FloodWait."""
    source = MediaSource("@channel", 42, None)
    target = tmp_path / "out.bin"
    fake = FakeDownloadTelegram([])
    fake.message.file.size = 20

    async def flood_after_one_chunk(
        media_obj, *, offset=0, request_size=None, **kwargs
    ):
        yield b"x"
        raise telethon_errors.FloodWaitError(request=None, capture=7)

    fake.iter_download = flood_after_one_chunk
    original_write_state = media._write_state

    def fail_after_initial_state(*args, **kwargs):
        if args[4] > 0:
            raise OSError("ENOSPC")
        return original_write_state(*args, **kwargs)

    monkeypatch.setattr(media, "_write_state", fail_after_initial_state)

    with pytest.raises(telethon_errors.FloodWaitError) as caught:
        await download_media(fake, source, "main", output=str(target))

    assert caught.value.seconds == 7


async def test_the_serial_part_file_is_synced_before_the_checkpoint_claims_it(
    tmp_path, monkeypatch
):
    """The sidecar fsyncs itself; an unsynced tail would outlive its record."""
    source = MediaSource("@channel", 42, None)
    target = tmp_path / "out.bin"
    fake = FakeDownloadTelegram([b"x"] * 16)
    fake.message.file.size = 16
    order: list[str] = []
    real_fsync = os.fsync
    monkeypatch.setattr(
        os, "fsync", lambda fd: (order.append("fsync"), real_fsync(fd))[1]
    )

    original_write_state = media._write_state

    def record_state(*args):
        order.append(f"checkpoint:{args[-1]}")
        original_write_state(*args)

    monkeypatch.setattr(media, "_write_state", record_state)

    await download_media(fake, source, "main", output=str(target))

    # order[0] is the initial "checkpoint:0" written before any bytes exist
    # (nothing to fsync yet, and `atomic.replace_text` fsyncs the state file
    # and its directory on every write). The part file's own fsync — the one
    # under test — must land immediately before the data checkpoint claims
    # the bytes it just made durable.
    checkpoint_16 = order.index("checkpoint:16")
    assert order[checkpoint_16 - 1] == "fsync"


class FakeParallelTelegram(FakeDownloadTelegram):
    def __init__(self):
        super().__init__([])
        self.message.file.size = 2 * 512 * 1024

    async def iter_download(self, media, *, offset=0, request_size=None, **kwargs):
        self.iter_download_calls.append(
            {"media": media, "offset": offset, "request_size": request_size, **kwargs}
        )
        yield bytes([65 + offset // (512 * 1024)]) * (512 * 1024)


async def test_parallel_download_maps_a_short_stream_to_policy_error(tmp_path):
    """CONTRACT: --parallel short stream exits 2 (PolicyError), never publishes."""

    class ShortParallelTelegram(FakeParallelTelegram):
        async def iter_download(self, media, *, offset=0, request_size=None, **kwargs):
            self.iter_download_calls.append(
                {
                    "media": media,
                    "offset": offset,
                    "request_size": request_size,
                    **kwargs,
                }
            )
            if offset == 0:
                yield bytes([65]) * (512 * 1024)
            # Second stripe ends with no bytes and no error.

    fake = ShortParallelTelegram()
    target = tmp_path / "out.bin"

    with pytest.raises(PolicyError, match=r"ended at .* bytes"):
        await download_media(
            fake,
            MediaSource("@channel", 42, None),
            "main",
            output=str(target),
            parallel=2,
        )

    assert not target.exists()


async def test_parallel_download_refuses_resuming_partial_transfer(tmp_path):
    source = MediaSource("@channel", 42, None)
    target = tmp_path / "out.bin"
    interrupted = FakeDownloadTelegram([b"old"], fail_after_first=True)
    # Same media as the parallel client below, so the guard under test is the
    # parallel-resume refusal and not the #180 changed-media restart.
    interrupted.message.file.size = 2 * 512 * 1024
    with pytest.raises(RuntimeError):
        await download_media(interrupted, source, "main", output=str(target))

    with pytest.raises(PolicyError, match="cannot resume"):
        await download_media(
            FakeParallelTelegram(), source, "main", output=str(target), parallel=2
        )


def _cross_filesystem_publish(monkeypatch) -> list[tuple[Path, Path]]:
    """Make every `.part` rename look like a cross-filesystem move (EXDEV)."""
    real_replace = os.replace
    renames: list[tuple[Path, Path]] = []

    def fake_replace(src, dst, **kwargs):
        renames.append((Path(src), Path(dst)))
        if str(src).endswith(".part"):
            raise OSError(errno.EXDEV, "Invalid cross-device link")
        return real_replace(src, dst, **kwargs)

    monkeypatch.setattr(os, "replace", fake_replace)
    return renames


async def test_download_publishes_onto_another_filesystem(tmp_path, monkeypatch):
    source = MediaSource("@channel", 42, None)
    target = tmp_path / "external" / "out.bin"
    fake = FakeDownloadTelegram([b"abc", b"def"])
    _cross_filesystem_publish(monkeypatch)

    result = await download_media(fake, source, "main", output=str(target))

    assert target.read_bytes() == b"abcdef"
    assert result["bytes"] == 6
    # No half-written leftover under the final name and no orphaned partial.
    assert list(target.parent.iterdir()) == [target]
    state_path, part_path = _state_paths(source)
    assert not part_path.exists()
    assert not state_path.exists()


async def test_parallel_download_publishes_onto_another_filesystem(
    tmp_path, monkeypatch
):
    source = MediaSource("@channel", 42, None)
    target = tmp_path / "external" / "out.bin"
    _cross_filesystem_publish(monkeypatch)

    result = await download_media(
        FakeParallelTelegram(), source, "main", output=str(target), parallel=2
    )

    assert target.read_bytes() == b"A" * (512 * 1024) + b"B" * (512 * 1024)
    assert result["bytes"] == 2 * 512 * 1024
    assert list(target.parent.iterdir()) == [target]
    state_path, part_path = _state_paths(source)
    assert not part_path.exists()
    assert not state_path.exists()


def _killed_parallel_transfer(source: MediaSource, destination: Path) -> Path:
    """Leave what a signal-killed parallel transfer leaves behind."""
    state_path, part_path = _state_paths(source)
    part_path.parent.mkdir(parents=True, exist_ok=True)
    part_path.write_bytes(b"scattered stripes")
    state_path.write_text(
        json.dumps(
            {
                "source": _source_label(source),
                "destination": str(destination),
                "offset": 0,
                "resumable": False,
            }
        )
    )
    return part_path


async def test_parallel_download_restarts_after_a_killed_transfer(tmp_path):
    source = MediaSource("@channel", 42, None)
    target = tmp_path / "out.bin"
    part_path = _killed_parallel_transfer(source, target)

    result = await download_media(
        FakeParallelTelegram(), source, "main", output=str(target), parallel=2
    )

    assert target.read_bytes() == b"A" * (512 * 1024) + b"B" * (512 * 1024)
    assert result["resumed"] is False
    assert not part_path.exists()


async def test_download_restarts_after_a_killed_parallel_transfer(tmp_path):
    source = MediaSource("@channel", 42, None)
    target = tmp_path / "out.bin"
    _killed_parallel_transfer(source, target)

    result = await download_media(
        FakeDownloadTelegram([b"abcdef"]), source, "main", output=str(target)
    )

    assert target.read_bytes() == b"abcdef"
    assert result["resumed"] is False


@pytest.mark.parametrize("payload", ["[]", '"x"', "42", "null", "true"])
def test_resume_offset_rejects_non_dict_state(tmp_path, payload):
    """Same shape guard as clone state: a valid-JSON non-object is corrupt."""
    source = MediaSource("@channel", 42, None)
    destination = tmp_path / "out.bin"
    state_path = tmp_path / "state.json"
    part_path = tmp_path / "out.part"
    part_path.write_bytes(b"partial")
    state_path.write_text(payload)

    with pytest.raises(PolicyError, match="state is invalid"):
        _resume_offset(state_path, part_path, source, destination, _target())


def test_resume_offset_rejects_truncated_utf8_state(tmp_path):
    source = MediaSource("@channel", 42, None)
    destination = tmp_path / "out.bin"
    state_path = tmp_path / "state.json"
    part_path = tmp_path / "out.part"
    part_path.write_bytes(b"partial")
    state_path.write_bytes(b'{"offset": 1, "source": "\xd0')

    with pytest.raises(PolicyError, match="state is invalid"):
        _resume_offset(state_path, part_path, source, destination, _target())


@pytest.mark.parametrize("offset", [True, -1, 8, 1.0, "1"])
def test_resume_offset_rejects_an_offset_that_is_not_a_byte_count(tmp_path, offset):
    """`true` is a JSON boolean, not byte 1 of the partial (clone resume agrees)."""
    source = MediaSource("@channel", 42, None)
    destination = tmp_path / "out.bin"
    state_path = tmp_path / "state.json"
    part_path = tmp_path / "out.part"
    part_path.write_bytes(b"partial")
    state_path.write_text(
        json.dumps(
            {
                "source": _source_label(source),
                "destination": str(destination),
                "offset": offset,
            }
        )
    )

    with pytest.raises(PolicyError, match="does not match requested output"):
        _resume_offset(state_path, part_path, source, destination, _target())
    assert part_path.read_bytes() == b"partial"


async def test_private_link_scans_dialogs_and_validates_channel():
    entity = make_channel(3817664407)
    message = type("Message", (), {"id": 878, "media": object()})()

    class FakeTelegram:
        async def iter_dialogs(self):
            yield type("Dialog", (), {"entity": entity})()

        async def get_input_entity(self, requested_entity):
            assert requested_entity is entity
            return "input-channel"

        async def __call__(self, request):
            assert request.id == ["input-channel"]

        async def get_messages(self, requested_entity, ids):
            assert requested_entity is entity
            assert ids == 878
            return message

    resolved_entity, target = await resolve_message(
        FakeTelegram(), MediaSource(None, 878, 3817664407), "main"
    )
    assert resolved_entity is entity
    assert target.media is message.media


async def test_private_link_without_dialog_names_account():
    class FakeTelegram:
        async def iter_dialogs(self):
            if False:
                yield None

    with pytest.raises(NotFoundError, match="account 'main' lacks access"):
        await resolve_message(FakeTelegram(), MediaSource(None, 8, 7), "main")


async def test_private_link_ignores_a_user_with_the_same_id():
    """t.me/c/<id> names a channel; a user whose id collides is not that peer."""
    entity = make_user(3817664407)

    class FakeTelegram:
        async def iter_dialogs(self):
            yield type("Dialog", (), {"entity": entity})()

        async def get_input_entity(self, requested_entity):
            raise AssertionError("a user must never be validated as a channel")

    with pytest.raises(NotFoundError, match="account 'main' lacks access"):
        await resolve_message(
            FakeTelegram(), MediaSource(None, 878, 3817664407), "main"
        )


async def test_private_link_channel_validation_names_account_on_denial():
    entity = make_channel(7)

    class FakeTelegram:
        async def iter_dialogs(self):
            yield type("Dialog", (), {"entity": entity})()

        async def get_input_entity(self, requested_entity):
            return "input-channel"

        async def __call__(self, request):
            raise telethon_errors.ChannelPrivateError(request=None)

    with pytest.raises(NotFoundError, match="account 'main' lacks access"):
        await resolve_message(FakeTelegram(), MediaSource(None, 8, 7), "main")


async def test_resolve_message_rejects_missing_media():
    entity = type("Entity", (), {"id": 12})()
    message = type("Message", (), {"id": 42, "media": None})()

    class FakeTelegram:
        async def get_entity(self, chat):
            return entity

        async def get_messages(self, requested_entity, ids):
            return message

    with pytest.raises(NotFoundError, match="downloadable media"):
        await resolve_message(FakeTelegram(), MediaSource("@channel", 42, None), "main")


class FakeIdentifiedTelegram(FakeDownloadTelegram):
    """A client whose message media carries a Telegram document id."""

    def __init__(self, chunks, *, document_id, **kwargs):
        super().__init__(chunks, **kwargs)
        self.message.media = ns(document=ns(id=document_id), photo=None)


async def test_resume_refuses_a_partial_file_from_replaced_media(tmp_path):
    """#180: the source swapped the file behind the same message.

    The resume record identified its partial file by path alone, so the new
    file's tail was appended to the old file's head and published as
    complete — no error, correct-looking output, wrong bytes.
    """
    source = MediaSource("@channel", 42, None)
    target = tmp_path / "out.bin"
    interrupted = FakeIdentifiedTelegram(
        [b"old"], document_id=111, fail_after_first=True
    )

    with pytest.raises(RuntimeError, match="network dropped"):
        await download_media(interrupted, source, "main", output=str(target))

    [state_path] = (tmp_path / "state" / "downloads").glob("*.json")
    assert json.loads(state_path.read_text())["offset"] == 3

    replaced = FakeIdentifiedTelegram([b"new"], document_id=222)
    replaced.message.file.size = len(b"new")
    result = await download_media(replaced, source, "main", output=str(target))

    assert replaced.iter_download_calls == [
        {"media": replaced.message.media, "offset": 0, "request_size": 512 * 1024}
    ]
    assert target.read_bytes() == b"new"
    assert result["resumed"] is False


async def test_resume_refuses_a_partial_file_whose_size_changed(tmp_path):
    """A same-id document re-encoded to a different length is not the same file."""
    source = MediaSource("@channel", 42, None)
    target = tmp_path / "out.bin"
    interrupted = FakeIdentifiedTelegram(
        [b"old"], document_id=111, fail_after_first=True
    )
    with pytest.raises(RuntimeError, match="network dropped"):
        await download_media(interrupted, source, "main", output=str(target))

    resized = FakeIdentifiedTelegram([b"new"], document_id=111)
    resized.message.file.size = len(b"new")

    result = await download_media(resized, source, "main", output=str(target))

    assert resized.iter_download_calls[0]["offset"] == 0
    assert target.read_bytes() == b"new"
    assert result["resumed"] is False


def _state(destination, *, media_id, size, offset=3):
    return json.dumps(
        {
            "source": "@channel:42",
            "destination": str(destination),
            "media_id": media_id,
            "size": size,
            "offset": offset,
        }
    )


def test_a_changed_media_never_masks_a_wrong_output_path(tmp_path):
    """Review finding: the identity check must not swallow the louder error.

    The state file is keyed by source alone, so a re-run with a different
    `--output` lands on the same record. That has always been exit 2; a
    source that also replaced its media in the meantime must not turn it
    into a silent restart.
    """
    source = MediaSource("@channel", 42, None)
    state_path = tmp_path / "state.json"
    part_path = tmp_path / "out.part"
    part_path.write_bytes(b"old")
    state_path.write_text(_state(tmp_path / "first.bin", media_id=111, size=6))

    with pytest.raises(PolicyError, match="does not match requested output"):
        _resume_offset(
            state_path,
            part_path,
            source,
            tmp_path / "second.bin",
            _target(ns(document=ns(id=222), photo=None), size=99),
        )

    # The louder error must not have destroyed the record on its way out.
    assert part_path.exists()
    assert state_path.exists()


async def test_download_state_dir_is_private_under_umask_022(tmp_path):
    import os

    source = MediaSource("@channel", 42, None)
    previous = os.umask(0o022)
    try:
        await download_media(
            FakeDownloadTelegram([b"abcdef"]),
            source,
            "main",
            output=str(tmp_path / "o"),
        )
    finally:
        os.umask(previous)

    _, part_path = _state_paths(source)
    assert part_path.parent.stat().st_mode & 0o777 == 0o700
    assert part_path.parent.parent.stat().st_mode & 0o777 == 0o700


class _ChunkedSource:
    """A voice message served in `CHUNK_SIZE` pieces, yielding to the loop
    between chunks so two downloads interleave; `drop_after` chunks then a
    dropped connection."""

    SIZE = 4 * 512 * 1024
    DATA = bytes(range(256)) * (SIZE // 256)

    def __init__(self, drop_after=None):
        self.drop_after = drop_after
        self.media = ns(document=ns(id=77))

    async def get_entity(self, ref):
        return ns(id=5)

    async def get_messages(self, entity, ids):
        file = ns(name="v.ogg", size=self.SIZE, ext=".ogg")
        return ns(id=ids, media=self.media, file=file)

    async def iter_download(self, media, offset, request_size):
        import asyncio

        for count, start in enumerate(range(offset, self.SIZE, request_size)):
            await asyncio.sleep(0)
            if self.drop_after is not None and count >= self.drop_after:
                raise ConnectionError("dropped")
            yield self.DATA[start : start + request_size]


async def test_two_runs_resuming_one_source_never_wedge_it(tmp_path):
    """both runs appended into one `.part`, recorded an
    offset past the media size, and every later run stopped there for good.
    The second run now refuses (exit 3) and the first one publishes."""
    import asyncio

    from tgcli.commands.media import MediaSource, download_media

    source = MediaSource(chat="5", message_id=9, private_channel_id=None)
    output = tmp_path / "v.ogg"
    with pytest.raises(ConnectionError):
        await download_media(
            _ChunkedSource(drop_after=1), source, "main", output=str(output)
        )

    results = await asyncio.gather(
        download_media(_ChunkedSource(), source, "main", output=str(output)),
        download_media(_ChunkedSource(), source, "main", output=str(output)),
        return_exceptions=True,
    )

    refused = [r for r in results if not isinstance(r, dict)]
    assert [getattr(r, "exit_code", r) for r in refused] == [3]
    assert [r["resumed"] for r in results if isinstance(r, dict)] == [True]
    assert output.read_bytes() == _ChunkedSource.DATA
