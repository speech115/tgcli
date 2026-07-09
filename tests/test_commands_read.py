import datetime as dt

import pytest

from tests.conftest import ns
from tgcli.commands.read import fetch_message, message_to_dict
from tgcli.errors import NotFoundError


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
        "from": {"id": 111, "name": "Alice"},
        "text": "hello",
        "media": None,
        "reply_to": None,
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
