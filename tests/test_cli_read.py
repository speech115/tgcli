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


def make_fake():
    entity = ns(id=-1001234, title="Channel")
    message = ns(
        id=42,
        date=dt.datetime(2026, 7, 6, 10, 0, tzinfo=dt.timezone.utc),
        sender_id=111,
        sender=ns(first_name="Alice", last_name=None),
        text="hello",
        media=None,
        reply_to_msg_id=None,
    )
    return FakeClient(messages=[message], entities={"@chan": entity})


def make_read_client():
    entity = ns(id=5, title="Chan")
    messages = [
        ns(
            id=message_id,
            date=dt.datetime(2026, 7, day, tzinfo=dt.timezone.utc),
            sender_id=111,
            sender=ns(first_name="Alice", last_name=None),
            text=f"message {message_id}",
            media=None,
            reply_to_msg_id=None,
            topic=100 if message_id == 2 else None,
        )
        for message_id, day in ((3, 18), (2, 17), (1, 16))
    ]
    return FakeClient(messages=messages, entities={"@chan": entity})


def test_read_before_and_after_id_map_to_offsets(config_env, monkeypatch, capsys):
    client = make_read_client()
    make_session_fake(monkeypatch, client)

    assert main(["read", "@chan", "--before-id", "3", "--after-id", "1", "--json"]) == 0

    data = json.loads(capsys.readouterr().out)
    assert [message["id"] for message in data["messages"]] == [2]
    assert client.iter_messages_kwargs["offset_id"] == 3
    assert client.iter_messages_kwargs["min_id"] == 1
    assert data["page"] == {"oldest_id": 2, "newest_id": 2}


def test_read_since_stops_at_boundary(config_env, monkeypatch, capsys):
    client = make_read_client()
    make_session_fake(monkeypatch, client)

    assert (
        main(["read", "@chan", "--since", "2026-07-18T00:00:00+00:00", "--json"]) == 0
    )

    data = json.loads(capsys.readouterr().out)
    assert all(message["date"] >= "2026-07-18" for message in data["messages"])


def test_read_topic_passes_reply_to(config_env, monkeypatch, capsys):
    client = make_read_client()
    make_session_fake(monkeypatch, client)

    assert main(["read", "@chan", "--topic", "100", "--json"]) == 0
    assert client.iter_messages_kwargs["reply_to"] == 100


def test_read_rejects_bad_since(config_env, capsys):
    assert main(["read", "@chan", "--since", "yesterday"]) == 1
    assert "--since expects an ISO 8601" in capsys.readouterr().err


def test_read_json_matches_contract(config_env, monkeypatch, capsys):
    make_session_fake(monkeypatch, make_fake())
    code = main(["--json", "read", "@chan", "--limit", "5"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data == {
        "dialog": {"id": -1001234, "name": "Channel"},
        "messages": [
            {
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
                "topic_id": None,
                "grouped_id": None,
                "is_service": False,
            }
        ],
        "page": {"oldest_id": 42, "newest_id": 42},
    }


def test_read_unknown_dialog_exits_4(config_env, monkeypatch, capsys):
    make_session_fake(monkeypatch, make_fake())
    code = main(["--json", "read", "@ghost"])
    assert code == 4
    assert json.loads(capsys.readouterr().err)["error"]["code"] == "NOT_FOUND"


def test_floodwait_maps_to_exit_5(config_env, monkeypatch, capsys):
    from telethon import errors as tg_errors

    fake = make_fake()

    async def flood(entity, limit=None, **kwargs):
        raise tg_errors.FloodWaitError(request=None, capture=42)
        yield  # pragma: no cover - makes this an async generator

    fake.iter_messages = flood
    make_session_fake(monkeypatch, fake)
    code = main(["--json", "read", "@chan"])
    assert code == 5
    err = json.loads(capsys.readouterr().err)["error"]
    assert err["code"] == "FLOOD_WAIT"
    assert err["retry_after"] == 42


def test_read_plain_sanitizes_sender_controls(config_env, monkeypatch, capsys):
    fake = make_fake()
    fake._messages[0].sender.first_name = "A\tlice\r\n"
    make_session_fake(monkeypatch, fake)

    code = main(["read", "@chan", "--plain"])

    assert code == 0
    output = capsys.readouterr().out
    assert output.count("\n") == 1
    assert "\r" not in output
    assert len(output.rstrip("\n").split("\t")) == 4
