import datetime as dt
import json

import pytest

from tests.conftest import FakeClient, make_session_fake, ns
from tgcli.cli import main


SAMPLE = """
default_account = "main"

[accounts.main]
api_id = 12345
api_hash = "abcdef0123456789"
"""


@pytest.fixture
def config_env(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE)
    monkeypatch.setenv("TGCLI_CONFIG", str(path))


def make_message(message_id, text):
    return ns(
        id=message_id,
        date=dt.datetime(2026, 7, 6, 10, 0, tzinfo=dt.timezone.utc),
        sender_id=111,
        sender=ns(first_name="Alice", last_name=None),
        text=text,
        media=None,
        reply_to_msg_id=None,
    )


def test_search_json_passes_query_and_returns_search_results(
    config_env, monkeypatch, capsys
):
    entity = ns(id=-1001234, title="Channel")
    fake = FakeClient(
        messages=[make_message(1, "other")],
        search_messages={"needle": [make_message(42, "needle result")]},
        entities={"@chan": entity},
    )
    make_session_fake(monkeypatch, fake)

    code = main(["search", "@chan", "needle", "--json"])

    assert code == 0
    assert fake.iter_messages_calls == [(entity, "needle", 20)]
    assert json.loads(capsys.readouterr().out) == {
        "dialog": {"id": -1001234, "name": "Channel"},
        "query": "needle",
        "messages": [
            {
                "id": 42,
                "date": "2026-07-06T10:00:00+00:00",
                "from": {"id": 111, "name": "Alice", "username": None},
                "text": "needle result",
                "media": None,
                "media_info": None,
                "reply_to": None,
                "permalink": None,
                "edited_at": None,
                "outgoing": False,
                "forwarded_from": None,
                "reactions": [],
                "topic_id": None,
                "grouped_id": None,
                "is_service": False,
            }
        ],
    }


def test_latest_json_returns_first_message(config_env, monkeypatch, capsys):
    entity = ns(id=-1001234, title="Channel")
    fake = FakeClient(
        messages=[make_message(42, "latest"), make_message(41, "older")],
        entities={"@chan": entity},
    )
    make_session_fake(monkeypatch, fake)

    code = main(["latest", "@chan", "--json"])

    assert code == 0
    assert fake.iter_messages_calls == [(entity, None, 1)]
    assert json.loads(capsys.readouterr().out) == {
        "dialog": {"id": -1001234, "name": "Channel"},
        "message": {
            "id": 42,
            "date": "2026-07-06T10:00:00+00:00",
            "from": {"id": 111, "name": "Alice", "username": None},
            "text": "latest",
            "media": None,
            "media_info": None,
            "reply_to": None,
            "permalink": None,
            "edited_at": None,
            "outgoing": False,
            "forwarded_from": None,
            "reactions": [],
            "topic_id": None,
            "grouped_id": None,
            "is_service": False,
        },
    }


@pytest.mark.parametrize(
    ("command", "fake"),
    [
        (
            ["search", "@chan", "needle", "--plain"],
            lambda entity: FakeClient(
                search_messages={"needle": [make_message(42, "a\tb\r\nc")]},
                entities={"@chan": entity},
            ),
        ),
        (
            ["latest", "@chan", "--plain"],
            lambda entity: FakeClient(
                messages=[make_message(42, "a\tb\r\nc")],
                entities={"@chan": entity},
            ),
        ),
    ],
)
def test_search_and_latest_plain_sanitize_message_controls(
    config_env, monkeypatch, capsys, command, fake
):
    entity = ns(id=-1001234, title="Channel")
    make_session_fake(monkeypatch, fake(entity))

    code = main(command)

    assert code == 0
    assert capsys.readouterr().out == "42\t2026-07-06T10:00:00+00:00\tAlice\ta b  c\n"


@pytest.mark.parametrize(
    ("command", "fake"),
    [
        (
            ["search", "@chan", "needle", "--plain"],
            lambda entity, message: FakeClient(
                search_messages={"needle": [message]}, entities={"@chan": entity}
            ),
        ),
        (
            ["latest", "@chan", "--plain"],
            lambda entity, message: FakeClient(
                messages=[message], entities={"@chan": entity}
            ),
        ),
    ],
)
def test_search_and_latest_plain_sanitize_sender_controls(
    config_env, monkeypatch, capsys, command, fake
):
    entity = ns(id=-1001234, title="Channel")
    message = make_message(42, "hello")
    message.sender.first_name = "A\tlice\r\n"
    make_session_fake(monkeypatch, fake(entity, message))

    code = main(command)

    assert code == 0
    output = capsys.readouterr().out
    assert output.count("\n") == 1
    assert "\r" not in output
    assert len(output.rstrip("\n").split("\t")) == 4
