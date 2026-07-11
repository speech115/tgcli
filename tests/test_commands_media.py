import json
from pathlib import Path

import pytest
from telethon import errors as telethon_errors

from tgcli.commands.media import (
    MediaSource,
    _resume_offset,
    _source_label,
    destination_for,
    parse_source,
    download_media,
    resolve_message,
    safe_filename,
)
from tgcli.errors import NotFoundError, PolicyError


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


async def test_download_throttles_state_writes_and_progress_updates(tmp_path, monkeypatch):
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
        fake, source, "main", output=str(target), progress=lambda current, total: progress_updates.append(current)
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
        await download_media(FakeParallelTelegram(), source, "main", output=str(target), parallel=2)


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


def test_resume_offset_discards_uncheckpointed_bytes(tmp_path):
    source = MediaSource("@channel", 42, None)
    destination = tmp_path / "out.bin"
    state_path = tmp_path / "state.json"
    part_path = tmp_path / "out.part"
    part_path.write_bytes(b"checkpointed-extra")
    state_path.write_text(json.dumps({
        "source": _source_label(source),
        "destination": str(destination),
        "offset": len(b"checkpointed"),
    }))

    assert _resume_offset(state_path, part_path, source, destination) == len(b"checkpointed")
    assert part_path.read_bytes() == b"checkpointed"


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
    entity = type("Entity", (), {"id": 3817664407})()
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


async def test_private_link_channel_validation_names_account_on_denial():
    entity = type("Entity", (), {"id": 7})()

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
        await resolve_message(
            FakeTelegram(), MediaSource("@channel", 42, None), "main"
        )
