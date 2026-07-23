import json
from datetime import UTC, datetime

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


def _msg(message_id, *, reply_to=None, text=None, replies=None):
    return ns(
        id=message_id,
        date=datetime(2026, 7, 20, 12, 0, tzinfo=UTC),
        sender_id=1,
        sender=ns(first_name="A", last_name=None, username=None),
        text=text or f"m{message_id}",
        media=None,
        reply_to_msg_id=reply_to,
        replies=replies,
        file=None,
        entities=None,
        edit_date=None,
        out=False,
        fwd_from=None,
        reactions=None,
        grouped_id=None,
        action=None,
        reply_to=None,
    )


def test_thread_walks_ancestors_oldest_first(config_env, monkeypatch, capsys):
    entity = ns(id=-1001, title="Chan", username="chan")
    messages = [
        _msg(1),
        _msg(2, reply_to=1),
        _msg(3, reply_to=2),
    ]
    client = FakeClient(messages=messages, entities={"@chan": entity})
    make_session_fake(monkeypatch, client)

    assert main(["--json", "thread", "@chan", "3"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["root"]["id"] == 3
    assert [m["id"] for m in payload["ancestors"]] == [1, 2]
    assert payload["replies"] == []
    assert payload["note"] is None


def test_thread_depth_bounds_ancestors(config_env, monkeypatch, capsys):
    entity = ns(id=-1001, title="Chan")
    messages = [_msg(1), _msg(2, reply_to=1), _msg(3, reply_to=2)]
    client = FakeClient(messages=messages, entities={"@chan": entity})
    make_session_fake(monkeypatch, client)

    assert main(["--json", "thread", "@chan", "3", "--depth", "1"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [m["id"] for m in payload["ancestors"]] == [2]


def test_thread_root_without_reply_has_empty_ancestors(config_env, monkeypatch, capsys):
    entity = ns(id=-1001, title="Chan")
    client = FakeClient(messages=[_msg(3)], entities={"@chan": entity})
    make_session_fake(monkeypatch, client)

    assert main(["--json", "thread", "@chan", "3"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ancestors"] == []
    assert payload["root"]["id"] == 3


def test_thread_replies_when_thread_exists(config_env, monkeypatch, capsys):
    entity = ns(id=-1001, title="Chan")
    root = _msg(3, replies=ns(replies=2, comments=True))
    replies = [_msg(10, reply_to=3, text="r1"), _msg(11, reply_to=3, text="r2")]
    client = FakeClient(
        messages=[root],
        entities={"@chan": entity},
        replies={3: replies},
    )
    make_session_fake(monkeypatch, client)

    assert main(["--json", "thread", "@chan", "3", "--replies"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert [m["id"] for m in payload["replies"]] == [10, 11]
    assert payload["note"] is None


def test_thread_replies_note_when_no_cheap_thread(config_env, monkeypatch, capsys):
    entity = ns(id=-1001, title="Chan")
    client = FakeClient(messages=[_msg(3)], entities={"@chan": entity})
    make_session_fake(monkeypatch, client)

    assert main(["--json", "thread", "@chan", "3", "--replies"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["replies"] == []
    assert payload["note"] == (
        "no cheap reply thread for this message; replies omitted"
    )


def test_thread_depth_hard_cap_limits_parent_fetches(config_env, monkeypatch, capsys):
    entity = ns(id=-1001, title="Chan")
    messages = [_msg(1)]
    for message_id in range(2, 152):
        messages.append(_msg(message_id, reply_to=message_id - 1))
    client = FakeClient(messages=messages, entities={"@chan": entity})
    make_session_fake(monkeypatch, client)

    assert main(["--json", "thread", "@chan", "151", "--depth", "999"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["ancestors"]) == 100
    parent_fetches = [
        call
        for call in client.get_messages_calls
        if call[1] is not None and call[3] is None and call[1] != 151
    ]
    assert len(parent_fetches) == 100
