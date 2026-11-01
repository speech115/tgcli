"""Media message and download fakes shared by the media tests."""

from tests.conftest import FakeClient


def media_message(message_id, kind, *, date=None, size=10, mime=None, filename=None):
    from datetime import UTC, datetime

    from tests.conftest import ns

    file = ns(name=filename, mime_type=mime, size=size)
    markers = {
        "photo": {
            "photo": object(),
            "video": None,
            "audio": None,
            "voice": None,
            "document": None,
        },
        "video": {
            "photo": None,
            "video": object(),
            "audio": None,
            "voice": None,
            "document": None,
        },
        "audio": {
            "photo": None,
            "video": None,
            "audio": object(),
            "voice": None,
            "document": None,
        },
        "voice": {
            "photo": None,
            "video": None,
            "audio": None,
            "voice": object(),
            "document": None,
        },
        "document": {
            "photo": None,
            "video": None,
            "audio": None,
            "voice": None,
            "document": object(),
        },
        "video_note": {
            "photo": None,
            "video": None,
            "audio": None,
            "voice": None,
            "document": None,
            "video_note": object(),
        },
    }[kind]
    return ns(
        id=message_id,
        date=date or datetime(2026, 7, 20, 12, 0, tzinfo=UTC),
        sender_id=1,
        sender=None,
        text="",
        media=object(),
        reply_to_msg_id=None,
        file=file,
        **markers,
    )


class StreamingClient(FakeClient):
    async def iter_download(self, media, *, offset=0, request_size=None, **kwargs):
        yield b"abc"[offset:]


def real_message(message_id, media):
    from datetime import UTC, datetime

    from telethon.tl import types

    return types.Message(
        id=message_id,
        peer_id=types.PeerChannel(5),
        date=datetime(2026, 7, 20, tzinfo=UTC),
        message="",
        media=media,
    )


def photo_media():
    from datetime import UTC, datetime

    from telethon.tl import types

    photo = types.Photo(
        id=7,
        access_hash=1,
        file_reference=b"",
        date=datetime(2026, 7, 20, tzinfo=UTC),
        sizes=[types.PhotoSize(type="y", w=1, h=1, size=3)],
        dc_id=2,
    )
    return types.MessageMediaPhoto(photo=photo)
