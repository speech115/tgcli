import datetime as dt
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from tests.conftest import ns
from tgcli.commands.read import fetch_message, message_to_dict
from tgcli.errors import NotFoundError


def _ns(**kwargs):
    return SimpleNamespace(**kwargs)


def test_message_to_dict_projects_message_contract():
    message = ns(
        id=42,
        date=dt.datetime(2026, 7, 6, 10, 0, tzinfo=dt.timezone.utc),
        sender_id=111,
        sender=ns(first_name="Alice", last_name=None),
        text="hello",
        media=None,
        reply_to_msg_id=None,
    )

    assert message_to_dict(message) == {
        "id": 42,
        "date": "2026-07-06T10:00:00+00:00",
        "from": {"id": 111, "name": "Alice", "username": None},
        "text": "hello",
        "media": None,
        "media_info": None,
        "reply_to": None,
        "permalink": None,
        "edited_at": None,
        "outgoing": False,
        "forwarded_from": None,
        "reactions": [],
        "custom_emoji": [],
        "topic_id": None,
        "grouped_id": None,
        "is_service": False,
    }


async def test_fetch_message_missing_message_raises_not_found():
    entity = ns(id=-1001234, title="Channel")

    class FakeTelegram:
        async def get_entity(self, chat):
            assert chat == "@chan"
            return entity

        async def get_messages(self, requested_entity, ids):
            assert requested_entity is entity
            assert ids == 42
            return None

    with pytest.raises(NotFoundError, match="message not found: 42"):
        await fetch_message(FakeTelegram(), "@chan", 42)


def test_message_to_dict_exposes_agent_fields():
    entity = _ns(id=1234, username="chan", broadcast=True)
    message = _ns(
        id=42,
        date=datetime(2026, 7, 18, 10, 0, tzinfo=UTC),
        sender_id=111,
        sender=_ns(first_name="Alice", last_name=None, username="alice"),
        text="hello",
        media=None,
        reply_to_msg_id=None,
        edit_date=datetime(2026, 7, 18, 11, 0, tzinfo=UTC),
        out=True,
        forward=_ns(
            from_name="Bob",
            sender_id=222,
            chat_id=None,
            date=datetime(2026, 7, 17, tzinfo=UTC),
        ),
        reactions=_ns(results=[_ns(reaction=_ns(emoticon="👍"), count=3)]),
        grouped_id=777,
        action=None,
        file=None,
    )
    data = message_to_dict(message, entity)
    assert data["permalink"] == "https://t.me/chan/42"
    assert data["edited_at"] == "2026-07-18T11:00:00+00:00"
    assert data["outgoing"] is True
    assert data["from"] == {"id": 111, "name": "Alice", "username": "alice"}
    assert data["forwarded_from"] == {
        "name": "Bob",
        "id": 222,
        "date": "2026-07-17T00:00:00+00:00",
    }
    assert data["reactions"] == [{"emoji": "👍", "count": 3}]
    assert data["grouped_id"] == 777
    assert data["is_service"] is False
    assert data["media_info"] is None
    assert data["topic_id"] is None


def test_message_to_dict_extracts_custom_emoji_ids():
    from telethon.tl.types import MessageEntityCustomEmoji

    # "🔥" is a surrogate pair (UTF-16 length 2): the entity covers offset 0..2.
    message = _ns(
        id=7,
        date=None,
        sender_id=1,
        sender=None,
        text="🔥 жги",
        message="🔥 жги",
        media=None,
        reply_to_msg_id=None,
        entities=[MessageEntityCustomEmoji(offset=0, length=2, document_id=5555)],
    )
    data = message_to_dict(message)
    assert data["custom_emoji"] == [
        {"id": 5555, "emoji": "🔥", "offset": 0, "length": 2}
    ]


def test_message_to_dict_media_topic_and_private_permalink():
    entity = _ns(id=999, username=None, megagroup=True)
    message = _ns(
        id=7,
        date=None,
        sender_id=1,
        sender=None,
        text="",
        media=_ns(),
        reply_to_msg_id=5,
        reply_to=_ns(forum_topic=True, reply_to_top_id=100, reply_to_msg_id=5),
        edit_date=None,
        out=False,
        forward=None,
        reactions=None,
        grouped_id=None,
        action=_ns(),
        file=_ns(
            name="doc.pdf",
            mime_type="application/pdf",
            size=100,
            duration=None,
            width=None,
            height=None,
        ),
    )
    data = message_to_dict(message, entity)
    assert data["permalink"] == "https://t.me/c/999/7"
    assert data["topic_id"] == 100
    assert data["is_service"] is True
    assert data["media_info"] == {
        "name": "doc.pdf",
        "mime": "application/pdf",
        "size": 100,
        "duration": None,
        "width": None,
        "height": None,
    }


def test_message_to_dict_minimal_namespace_still_works():
    message = _ns(
        id=1,
        date=None,
        sender_id=None,
        sender=None,
        text=None,
        media=None,
        reply_to_msg_id=None,
    )
    data = message_to_dict(message)
    assert data["permalink"] is None
    assert data["media_info"] is None
    assert data["reactions"] == []
    assert data["outgoing"] is False
