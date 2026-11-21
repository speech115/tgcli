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
    _, part_path = _state_paths(source)
    assert part_path.read_bytes() == b"only"


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


class ShortParallelTelegram(FakeParallelTelegram):
    """The second stream ends with no bytes and no error."""

    async def iter_download(self, media, *, offset=0, request_size=None, **kwargs):
        self.iter_download_calls.append(
            {"media": media, "offset": offset, "request_size": request_size, **kwargs}
        )
        if offset == 0:
            yield bytes([65]) * (512 * 1024)


async def test_parallel_download_maps_a_short_stream_to_policy_error(tmp_path):
    """CONTRACT: a short stream exits 2 (PolicyError), never publishes."""
    target = tmp_path / "out.bin"

    with pytest.raises(PolicyError, match=r"stopped at 524288/1048576 bytes"):
        await download_media(
            ShortParallelTelegram(),
            MediaSource("@channel", 42, None),
            "main",
            output=str(target),
            parallel=2,
        )

    assert not target.exists()


async def test_parallel_download_resumes_an_interrupted_transfer(tmp_path):
    source = MediaSource("@channel", 42, None)
    target = tmp_path / "out.bin"
    with pytest.raises(PolicyError, match="stopped at"):
        await download_media(
            ShortParallelTelegram(), source, "main", output=str(target), parallel=2
        )

    fake = FakeParallelTelegram()
    result = await download_media(fake, source, "main", output=str(target), parallel=2)

    assert result["resumed"] is True
    assert [call["offset"] for call in fake.iter_download_calls] == [512 * 1024]
    assert target.read_bytes() == b"A" * (512 * 1024) + b"B" * (512 * 1024)


async def test_download_uses_four_streams_by_default(tmp_path):
    source = MediaSource("@channel", 42, None)
    target = tmp_path / "out.bin"
    fake = FakeParallelTelegram()
    fake.message.file.size = 4 * 512 * 1024

    result = await download_media(fake, source, "main", output=str(target))

    assert result["parallel"] == 4
    assert sorted(call["offset"] for call in fake.iter_download_calls) == [
        index * 512 * 1024 for index in range(4)
    ]
    assert {call["stride"] for call in fake.iter_download_calls} == {4 * 512 * 1024}
    assert target.read_bytes() == b"".join(
        bytes([65 + index]) * (512 * 1024) for index in range(4)
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
