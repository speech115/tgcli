from types import SimpleNamespace as NS

from telethon.tl import types

from tgcli.mirror_probe import classify_message, empty_capability


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
