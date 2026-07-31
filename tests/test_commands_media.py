import errno
import json
import os
import shutil
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
    destination_for,
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


def test_parse_source_accepts_public_link():
    assert parse_source("https://t.me/example_channel/42", None) == MediaSource(
        chat="@example_channel", message_id=42, private_channel_id=None
    )


def test_parse_source_accepts_private_link():
    assert parse_source("https://t.me/c/3817664407/878", None) == MediaSource(
        chat=None, message_id=878, private_channel_id=3817664407
    )


def test_parse_source_accepts_chat_and_message_id():
    assert parse_source("@channel", 42) == MediaSource(
        chat="@channel", message_id=42, private_channel_id=None
    )


def test_parse_source_rejects_incomplete_reference():
    with pytest.raises(NotFoundError, match="media source"):
        parse_source("@channel", None)


def test_safe_filename_cannot_escape_destination():
    assert safe_filename("../../a\tb.mp4", 42) == "a b.mp4"


def test_safe_filename_uses_message_id_when_name_is_empty():
    assert safe_filename("..", 42) == "media-42.bin"


def test_safe_filename_is_capped_to_a_portable_byte_length():
    assert len(safe_filename("a" * 300 + ".bin", 42).encode()) <= 200


def test_destination_refuses_existing_final_path(tmp_path):
    target = tmp_path / "already-there.bin"
    target.write_bytes(b"done")

    with pytest.raises(PolicyError, match="already exists"):
        destination_for("ignored.bin", str(target))


def test_destination_defaults_to_downloads(monkeypatch, tmp_path):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)

    assert destination_for("file.bin", None) == tmp_path / "Downloads" / "file.bin"


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


async def test_download_resumes_from_existing_partial_transfer(tmp_path):
    source = MediaSource("@channel", 42, None)
    target = tmp_path / "out.bin"
    interrupted = FakeDownloadTelegram([b"old"], fail_after_first=True)

    with pytest.raises(RuntimeError, match="network dropped"):
        await download_media(interrupted, source, "main", output=str(target))

    [state_path] = (tmp_path / "state" / "downloads").glob("*.json")
    assert json.loads(state_path.read_text())["offset"] == 3

    resumed = FakeDownloadTelegram([b"new"])
    result = await download_media(resumed, source, "main", output=str(target))

    assert resumed.iter_download_calls == [
        {"media": resumed.message.media, "offset": 3, "request_size": 512 * 1024}
    ]
    assert target.read_bytes() == b"oldnew"
    assert result == {
        "source": "@channel:42",
        "path": str(target),
        "bytes": 6,
        "resumed": True,
        "parallel": 1,
    }


async def test_download_throttles_state_writes_and_progress_updates(
    tmp_path, monkeypatch
):
    source = MediaSource("@channel", 42, None)
    target = tmp_path / "out.bin"
    fake = FakeDownloadTelegram([b"x"] * 17)
    state_writes = []
    progress_updates = []
    from tgcli.commands import media

    original_write_state = media._write_state

    def record_state(*args):
        state_writes.append(args[-1])
        original_write_state(*args)

    monkeypatch.setattr(media, "_write_state", record_state)

    await download_media(
        fake,
        source,
        "main",
        output=str(target),
        progress=lambda current, total: progress_updates.append(current),
    )

    assert state_writes == [0, 16, 17]
    assert progress_updates == [16, 17]


async def test_download_refuses_existing_final_path(tmp_path):
    target = tmp_path / "out.bin"
    target.write_bytes(b"done")
    fake = FakeDownloadTelegram([b"ignored"])

    with pytest.raises(PolicyError, match="already exists"):
        await download_media(
            fake, MediaSource("@channel", 42, None), "main", output=str(target)
        )

    assert fake.iter_download_calls == []


class FakeParallelTelegram(FakeDownloadTelegram):
    def __init__(self):
        super().__init__([])
        self.message.file.size = 2 * 512 * 1024

    async def iter_download(self, media, *, offset=0, request_size=None, **kwargs):
        self.iter_download_calls.append(
            {"media": media, "offset": offset, "request_size": request_size, **kwargs}
        )
        yield bytes([65 + offset // (512 * 1024)]) * (512 * 1024)


async def test_parallel_download_uses_disjoint_offsets(tmp_path):
    fake = FakeParallelTelegram()
    target = tmp_path / "out.bin"

    result = await download_media(
        fake, MediaSource("@channel", 42, None), "main", output=str(target), parallel=2
    )

    assert {call["offset"] for call in fake.iter_download_calls} == {0, 512 * 1024}
    assert {call["stride"] for call in fake.iter_download_calls} == {2 * 512 * 1024}
    assert target.read_bytes() == b"A" * (512 * 1024) + b"B" * (512 * 1024)
    assert result["parallel"] == 2


async def test_parallel_download_refuses_resuming_partial_transfer(tmp_path):
    source = MediaSource("@channel", 42, None)
    target = tmp_path / "out.bin"
    interrupted = FakeDownloadTelegram([b"old"], fail_after_first=True)
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


async def test_download_publishes_with_a_rename_on_one_filesystem(
    tmp_path, monkeypatch
):
    source = MediaSource("@channel", 42, None)
    target = tmp_path / "out.bin"
    fake = FakeDownloadTelegram([b"abcdef"])
    real_replace = os.replace
    renames: list[tuple[Path, Path]] = []

    def spy(src, dst, **kwargs):
        renames.append((Path(src), Path(dst)))
        return real_replace(src, dst, **kwargs)

    def no_copy(*args, **kwargs):
        raise AssertionError("same-filesystem publish must stay an atomic rename")

    monkeypatch.setattr(os, "replace", spy)
    monkeypatch.setattr(shutil, "copyfile", no_copy)

    await download_media(fake, source, "main", output=str(target))

    _, part_path = _state_paths(source)
    assert (part_path, target) in renames
    assert target.read_bytes() == b"abcdef"


async def test_parallel_download_records_a_non_resumable_transfer(
    tmp_path, monkeypatch
):
    source = MediaSource("@channel", 42, None)
    target = tmp_path / "out.bin"
    state_path, _ = _state_paths(source)
    in_flight: list[str] = []
    real_striped = media.download_striped

    async def spy(*args, **kwargs):
        if state_path.exists():
            in_flight.append(state_path.read_text())
        return await real_striped(*args, **kwargs)

    monkeypatch.setattr(media, "download_striped", spy)

    await download_media(
        FakeParallelTelegram(), source, "main", output=str(target), parallel=2
    )

    assert [json.loads(text) for text in in_flight] == [
        {
            "source": "@channel:42",
            "destination": str(target),
            "offset": 0,
            "resumable": False,
        }
    ]
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


def test_resume_offset_raises_policy_error_when_part_file_missing(tmp_path):
    source = MediaSource("@channel", 42, None)
    destination = tmp_path / "out.bin"
    state_path = tmp_path / "state.json"
    part_path = tmp_path / "missing.part"
    state_path.write_text(
        json.dumps(
            {
                "source": _source_label(source),
                "destination": str(destination),
                "offset": 0,
            }
        )
    )

    with pytest.raises(PolicyError, match="no partial file"):
        _resume_offset(state_path, part_path, source, destination)


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
        _resume_offset(state_path, part_path, source, destination)


def test_resume_offset_rejects_truncated_utf8_state(tmp_path):
    source = MediaSource("@channel", 42, None)
    destination = tmp_path / "out.bin"
    state_path = tmp_path / "state.json"
    part_path = tmp_path / "out.part"
    part_path.write_bytes(b"partial")
    state_path.write_bytes(b'{"offset": 1, "source": "\xd0')

    with pytest.raises(PolicyError, match="state is invalid"):
        _resume_offset(state_path, part_path, source, destination)


def test_resume_offset_discards_uncheckpointed_bytes(tmp_path):
    source = MediaSource("@channel", 42, None)
    destination = tmp_path / "out.bin"
    state_path = tmp_path / "state.json"
    part_path = tmp_path / "out.part"
    part_path.write_bytes(b"checkpointed-extra")
    state_path.write_text(
        json.dumps(
            {
                "source": _source_label(source),
                "destination": str(destination),
                "offset": len(b"checkpointed"),
            }
        )
    )

    assert _resume_offset(state_path, part_path, source, destination) == len(
        b"checkpointed"
    )
    assert part_path.read_bytes() == b"checkpointed"


async def test_download_restarts_when_partial_has_no_state(tmp_path):
    """A partial nobody checkpointed proves nothing: start over, never wedge."""
    source = MediaSource("@channel", 42, None)
    target = tmp_path / "out.bin"
    state_path, part_path = _state_paths(source)
    part_path.parent.mkdir(parents=True, exist_ok=True)
    part_path.write_bytes(b"unproven")

    result = await download_media(
        FakeDownloadTelegram([b"fresh"]), source, "main", output=str(target)
    )

    assert target.read_bytes() == b"fresh"
    assert result["resumed"] is False
    assert not part_path.exists()
    assert not state_path.exists()


def test_resume_offset_restarts_when_state_is_missing(tmp_path):
    source = MediaSource("@channel", 42, None)
    destination = tmp_path / "out.bin"
    part_path = tmp_path / "out.part"
    part_path.write_bytes(b"unproven")

    assert _resume_offset(tmp_path / "state.json", part_path, source, destination) == 0
    assert not part_path.exists()


async def test_bulk_download_skips_existing_destinations_and_continues(
    tmp_path, monkeypatch
):
    entity = ns(id=-1001, title="Channel")
    first = ns(
        id=41,
        date=None,
        sender_id=1,
        sender=None,
        text="",
        media=object(),
        reply_to_msg_id=None,
        file=ns(name="already.bin", size=10, mime_type="application/octet-stream"),
        photo=None,
        video=None,
        audio=None,
        voice=None,
        document=object(),
    )
    second = ns(
        id=42,
        date=None,
        sender_id=1,
        sender=None,
        text="",
        media=object(),
        reply_to_msg_id=None,
        file=ns(name="fresh.bin", size=20, mime_type="application/octet-stream"),
        photo=None,
        video=None,
        audio=None,
        voice=None,
        document=object(),
    )

    class FakeTelegram:
        async def get_entity(self, chat):
            assert chat == "@channel"
            return entity

        async def get_messages(self, requested_entity, ids):
            assert requested_entity is entity
            return {41: first, 42: second}[ids]

    async def fake_download_media(tg, source, account_alias, **kwargs):
        target = Path(kwargs["output"])
        if source.message_id == 41:
            raise PolicyError(f"output path already exists: {target}")
        assert source.message_id == 42
        return {
            "source": "@channel:42",
            "path": str(target),
            "bytes": 20,
            "resumed": False,
            "parallel": 1,
        }

    monkeypatch.setattr(media, "download_media", fake_download_media)

    data = await media.download_media_bulk(
        FakeTelegram(),
        "@channel",
        "main",
        message_ids=[41, 42],
        output=str(tmp_path),
    )

    assert data == {
        "dialog": {"id": -1001, "name": "Channel"},
        "items": [
            {
                "message_id": 42,
                "path": str(tmp_path / "fresh.bin"),
                "bytes": 20,
                "resumed": False,
            }
        ],
        "count": 1,
        "failed": [],
        "skipped": [
            {
                "message_id": 41,
                "reason": f"output path already exists: {tmp_path / 'already.bin'}",
            }
        ],
    }


async def test_resolve_message_uses_public_chat_reference():
    entity = type("Entity", (), {"id": 12})()
    message = type("Message", (), {"id": 42, "media": object()})()

    class FakeTelegram:
        async def get_entity(self, chat):
            assert chat == "@channel"
            return entity

        async def get_messages(self, requested_entity, ids):
            assert requested_entity is entity
            assert ids == 42
            return message

    assert await resolve_message(
        FakeTelegram(), MediaSource("@channel", 42, None), "main"
    ) == (entity, message)


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

    assert await resolve_message(
        FakeTelegram(), MediaSource(None, 878, 3817664407), "main"
    ) == (entity, message)


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
