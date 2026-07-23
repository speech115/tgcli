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


def make_fake(message_ids=(42,)):
    entity = ns(id=-1001234, title="Channel")
    messages = [
        ns(
            id=message_id,
            date=dt.datetime(2026, 7, 6, 10, 0, tzinfo=dt.timezone.utc),
            sender_id=111,
            sender=ns(first_name="Alice", last_name=None),
            text="hello",
            media=None,
            reply_to_msg_id=None,
        )
        for message_id in message_ids
    ]
    return FakeClient(messages=messages, entities={"@chan": entity})


def test_message_json_matches_contract(config_env, monkeypatch, capsys):
    make_session_fake(monkeypatch, make_fake())

    code = main(["message", "@chan", "42", "--json"])

    assert code == 0
    assert json.loads(capsys.readouterr().out) == {
        "dialog": {"id": -1001234, "name": "Channel"},
        "message": {
            "id": 42,
            "date": "2026-07-06T10:00:00+00:00",
            "from": {"id": 111, "name": "Alice", "username": None},
            "text": "hello",
            "media": None,
            "media_info": None,
            "reply_to": None,
            "quote_text": None,
            "permalink": None,
            "edited_at": None,
            "outgoing": False,
            "forwarded_from": None,
            "reactions": [],
            "custom_emoji": [],
            "topic_id": None,
            "grouped_id": None,
            "is_service": False,
        },
    }


def test_message_missing_id_exits_4(config_env, monkeypatch, capsys):
    make_session_fake(monkeypatch, make_fake())

    code = main(["message", "@chan", "99", "--json"])

    assert code == 4
    assert json.loads(capsys.readouterr().err)["error"]["code"] == "NOT_FOUND"


def test_message_context_returns_neighbors(config_env, monkeypatch, capsys):
    make_session_fake(monkeypatch, make_fake(message_ids=(1, 2, 3)))

    assert main(["message", "@chan", "2", "--context", "1", "--json"]) == 0

    data = json.loads(capsys.readouterr().out)
    assert data["message"]["id"] == 2
    assert [message["id"] for message in data["context"]] == [1, 3]


def test_message_context_does_not_expand_sparse_id_window(
    config_env, monkeypatch, capsys
):
    make_session_fake(monkeypatch, make_fake(message_ids=(9, 10, 12)))

    assert main(["message", "@chan", "10", "--context", "1", "--json"]) == 0

    data = json.loads(capsys.readouterr().out)
    assert [message["id"] for message in data["context"]] == [9]


def test_message_plain_sanitizes_message_controls(config_env, monkeypatch, capsys):
    fake = make_fake()
    fake._messages[0].text = "a\tb\r\nc"
    make_session_fake(monkeypatch, fake)

    code = main(["message", "@chan", "42", "--plain"])

    assert code == 0
    assert capsys.readouterr().out == "42\t2026-07-06T10:00:00+00:00\tAlice\ta b  c\n"


def test_message_plain_sanitizes_sender_controls(config_env, monkeypatch, capsys):
    fake = make_fake()
    fake._messages[0].sender.first_name = "A\tlice\r\n"
    make_session_fake(monkeypatch, fake)

    code = main(["message", "@chan", "42", "--plain"])

    assert code == 0
    output = capsys.readouterr().out
    assert output.count("\n") == 1
    assert "\r" not in output
    assert len(output.rstrip("\n").split("\t")) == 4
