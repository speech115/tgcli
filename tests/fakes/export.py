"""Export fakes shared by the export tests."""

import datetime as dt

from tests.conftest import FakeClient, ns

SAMPLE = """
default_account = "main"

[accounts.main]
api_id = 12345
api_hash = "abcdef0123456789"
"""


def make_message(message_id, text):
    return ns(
        id=message_id,
        date=dt.datetime(2026, 7, 10, 10, 0, tzinfo=dt.UTC),
        sender_id=111,
        sender=ns(first_name="Alice", last_name=None),
        text=text,
        media=None,
        reply_to_msg_id=None,
    )


def make_export_fake(messages=(), participants=()):
    entity = ns(id=-1001234, title="Channel")
    return FakeClient(
        messages=messages,
        participants=participants,
        entities={"@chan": entity},
    )
