import hashlib
import json
from types import SimpleNamespace as NS

import pytest
from telethon.tl import types

from tgcli.mirror_probe import (
    classify_message,
    empty_capability,
    probe_chat,
    probe_message,
    write_report,
)


def message(media=None, *, text="", grouped_id=None, reply_to=None, noforwards=True):
    return NS(
        id=42,
        media=media,
        message=text,
        entities=[],
        grouped_id=grouped_id,
        reply_to=reply_to,
        noforwards=noforwards,
        action=None,
    )


def test_classifies_core_message_families():
    assert classify_message(message(text="hello")) == "text"
    assert classify_message(message(types.MessageMediaPhoto(photo=None))) == "photo"
    assert classify_message(message(types.MessageMediaPoll(poll=None, results=None))) == "poll"
    assert classify_message(message(types.MessageMediaUnsupported())) == "unsupported"


def test_classifies_document_subtypes_from_attributes():
    voice = NS(
        mime_type="audio/ogg",
        attributes=[types.DocumentAttributeAudio(duration=1, voice=True)],
    )
    round_video = NS(
        mime_type="video/mp4",
        attributes=[
            types.DocumentAttributeVideo(
                duration=1, w=320, h=320, round_message=True, supports_streaming=True
            )
        ],
    )
    assert classify_message(message(types.MessageMediaDocument(document=voice))) == "voice"
    assert classify_message(message(types.MessageMediaDocument(document=round_video))) == "video_note"


def test_animation_classification_does_not_depend_on_server_attribute_order():
    animation = NS(
        mime_type="video/mp4",
        attributes=[
            types.DocumentAttributeVideo(duration=1, w=64, h=64),
            types.DocumentAttributeAnimated(),
            types.DocumentAttributeFilename("animation.mp4"),
        ],
    )
    assert classify_message(
        message(types.MessageMediaDocument(document=animation))
    ) == "animation"

    round_animation = NS(
        mime_type="video/mp4",
        attributes=[
            types.DocumentAttributeVideo(
                duration=1, w=240, h=240, round_message=True
            ),
            types.DocumentAttributeAnimated(),
        ],
    )
    assert classify_message(
        message(types.MessageMediaDocument(document=round_animation))
    ) == "video_note"


def test_empty_capability_starts_unproven():
    assert empty_capability("video", 42) == {
        "kind": "video",
        "sample_message_id": 42,
        "decode": "pass",
        "telethon_bytes": "not_tested",
        "bytes": None,
        "sha256": None,
        "error": None,
    }


class DownloadFake:
    def __init__(self, chunks, error=None):
        self.chunks = chunks
        self.error = error

    async def iter_download(self, media, request_size=None):
        for chunk in self.chunks:
            yield chunk
        if self.error:
            raise self.error


class ChatFake(DownloadFake):
    def __init__(self, messages, entity, chunks=None):
        super().__init__([b"photo"] if chunks is None else chunks)
        self.messages = messages
        self.entity = entity
        self.requested_chat = None

    async def get_entity(self, chat):
        self.requested_chat = chat
        return self.entity

    async def iter_messages(self, entity, limit=None):
        for item in self.messages[:limit]:
            yield item


@pytest.mark.asyncio
async def test_probe_message_hashes_the_complete_stream():
    payload = [b"abc", b"def"]
    result = await probe_message(
        DownloadFake(payload),
        message(types.MessageMediaPhoto(photo=NS(id=1))),
    )
    assert result["telethon_bytes"] == "pass"
    assert result["bytes"] == 6
    assert result["sha256"] == hashlib.sha256(b"abcdef").hexdigest()


@pytest.mark.asyncio
async def test_probe_message_rejects_zero_bytes():
    result = await probe_message(
        DownloadFake([]),
        message(types.MessageMediaPhoto(photo=NS(id=1))),
    )
    assert result["telethon_bytes"] == "fail"
    assert result["error"] == "zero_bytes"


@pytest.mark.asyncio
async def test_probe_message_marks_interruption_inconclusive():
    result = await probe_message(
        DownloadFake([b"partial"], ConnectionError("offline")),
        message(types.MessageMediaPhoto(photo=NS(id=1))),
    )
    assert result["telethon_bytes"] == "inconclusive"
    assert result["bytes"] == 7
    assert result["sha256"] is None
    assert result["error"] == "ConnectionError"


@pytest.mark.asyncio
async def test_probe_message_redacts_unsupported_media_class_name():
    private_media_type = type("DO_NOT_LEAK_TL_CLASS_91c8", (), {})
    result = await probe_message(DownloadFake([]), message(private_media_type()))
    assert result["decode"] == "unsupported"
    assert result["error"] == "unsupported_media"
    assert "DO_NOT_LEAK_TL_CLASS_91c8" not in json.dumps(result)


@pytest.mark.asyncio
async def test_probe_chat_aggregates_samples_per_kind_and_redacts_identity():
    source = ChatFake(
        [
            message(text="secret"),
            message(types.MessageMediaPhoto(photo=NS(id=1))),
            message(types.MessageMediaPhoto(photo=NS(id=2))),
        ],
        NS(id=999, title="Private title", username="private_name", noforwards=True),
    )
    report = await probe_chat(
        source,
        "Private title",
        123,
        role="owned",
        limit=100,
        samples_per_kind=3,
    )
    encoded = json.dumps(report)
    assert report["source"]["protected"] is True
    assert report["source"]["role"] == "owned"
    assert len(report["source"]["fingerprint"]) == 16
    assert {item["kind"] for item in report["capabilities"]} == {"text", "photo"}
    photo = next(item for item in report["capabilities"] if item["kind"] == "photo")
    assert photo["sample_count"] == 2
    assert photo["coverage"] == "limited"
    assert photo["telethon_bytes"] == "pass"
    assert "Private title" not in encoded
    assert "private_name" not in encoded
    assert "secret" not in encoded
    assert "999" not in encoded


@pytest.mark.asyncio
async def test_probe_chat_parses_numeric_chat_reference_before_resolution():
    source = ChatFake([], NS(id=999, noforwards=False))
    await probe_chat(source, "-1001234567890", 123, role="lab", limit=1)
    assert source.requested_chat == -1001234567890
    assert isinstance(source.requested_chat, int)


@pytest.mark.asyncio
async def test_probe_chat_caps_samples_and_marks_mixed_states_inconclusive():
    source = ChatFake(
        [message(types.MessageMediaPhoto(photo=NS(id=index))) for index in range(5)],
        NS(id=888, noforwards=False),
        chunks=[],
    )
    report = await probe_chat(
        source, "chat", 123, role="lab", limit=100, samples_per_kind=3
    )
    photo = report["capabilities"][0]
    assert report["source"]["scanned"] == 5
    assert photo["sample_count"] == 3
    assert photo["coverage"] == "complete"

    photo["samples"][0]["telethon_bytes"] = "pass"
    from tgcli.mirror_probe import _aggregate_kind

    mixed = _aggregate_kind("photo", photo["samples"], 3)
    assert mixed["telethon_bytes"] == "inconclusive"


@pytest.mark.asyncio
async def test_probe_chat_rejects_invalid_role_and_sample_limit():
    source = ChatFake([], NS(id=1, noforwards=False))
    with pytest.raises(ValueError, match="invalid probe role"):
        await probe_chat(source, "chat", 123, role="admin", limit=10)
    with pytest.raises(ValueError, match="samples_per_kind"):
        await probe_chat(
            source, "chat", 123, role="lab", limit=10, samples_per_kind=4
        )


@pytest.mark.asyncio
async def test_probe_report_does_not_leak_private_payload_fields():
    sentinel = "DO_NOT_LEAK_7f4d2a"
    document = NS(
        mime_type="application/octet-stream",
        attributes=[types.DocumentAttributeFilename(file_name=f"{sentinel}.bin")],
    )
    private_message = message(
        types.MessageMediaDocument(document=document), text=sentinel
    )
    private_message.sender = NS(first_name=sentinel, last_name=sentinel)
    source = ChatFake(
        [private_message],
        NS(id=999, title=sentinel, username=sentinel, phone=sentinel, noforwards=True),
    )
    report = await probe_chat(
        source,
        sentinel,
        123,
        role="subscriber",
        limit=100,
        samples_per_kind=3,
    )
    assert sentinel not in json.dumps(report, ensure_ascii=False)


def test_write_report_is_atomic(tmp_path):
    destination = tmp_path / "probe.json"
    write_report(destination, {"probe_version": 1})
    assert json.loads(destination.read_text()) == {"probe_version": 1}
    assert not list(tmp_path.glob("*.tmp"))
