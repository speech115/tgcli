import hashlib
from types import SimpleNamespace as NS

import pytest
from telethon.tl import types

from tgcli.mirror_probe import classify_message, empty_capability, probe_message


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
