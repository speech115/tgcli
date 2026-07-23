import csv
import datetime as dt
import json
import asyncio

import pytest
from telethon import errors as telethon_errors

from tests.conftest import FakeClient, make_session_fake, ns
from tgcli.cli import main
from tgcli.commands.export import _atomic_text_destination


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
        date=dt.datetime(2026, 7, 10, 10, 0, tzinfo=dt.timezone.utc),
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


def test_export_messages_requires_output(config_env, capsys):
    assert main(["export", "messages", "@chan"]) == 1
    assert "--output" in capsys.readouterr().err


def test_export_messages_json_initializes_takeout_and_writes_oldest_first(
    config_env, monkeypatch, tmp_path, capsys
):
    fake = make_export_fake(
        messages=[make_message(2, "newer"), make_message(1, "older")]
    )
    make_session_fake(monkeypatch, fake)
    destination = tmp_path / "messages.jsonl"

    assert (
        main(["--json", "export", "messages", "@chan", "--output", str(destination)])
        == 0
    )

    assert json.loads(capsys.readouterr().out) == {
        "export": {
            "kind": "messages",
            "format": "jsonl",
            "path": str(destination),
            "count": 2,
            "dialog": {"id": -1001234, "name": "Channel"},
        }
    }
    assert [
        json.loads(line)["id"] for line in destination.read_text().splitlines()
    ] == [1, 2]
    assert fake.takeout_calls == [{"chats": True, "megagroups": True, "channels": True}]
    assert fake.iter_messages_reverse_calls == [True]


def test_export_messages_reuses_valid_existing_takeout(
    config_env, monkeypatch, tmp_path
):
    fake = make_export_fake(messages=[make_message(1, "message")])
    fake.session.takeout_id = 42
    make_session_fake(monkeypatch, fake)

    assert (
        main(
            [
                "export",
                "messages",
                "@chan",
                "--output",
                str(tmp_path / "messages.jsonl"),
            ]
        )
        == 0
    )
    assert fake.takeout_calls == [{}]


def test_export_messages_discards_malformed_takeout_id(
    config_env, monkeypatch, tmp_path
):
    fake = make_export_fake(messages=[make_message(1, "message")])
    fake.session.takeout_id = b""
    make_session_fake(monkeypatch, fake)

    assert (
        main(
            [
                "export",
                "messages",
                "@chan",
                "--output",
                str(tmp_path / "messages.jsonl"),
            ]
        )
        == 0
    )
    assert fake.session.takeout_id is None
    assert fake.takeout_calls == [{"chats": True, "megagroups": True, "channels": True}]


def test_export_messages_streams_ten_thousand_messages(
    config_env, monkeypatch, tmp_path, capsys
):
    messages = [
        make_message(message_id, f"message {message_id}")
        for message_id in range(10_000, 0, -1)
    ]
    fake = make_export_fake(messages=messages)
    make_session_fake(monkeypatch, fake)
    destination = tmp_path / "messages.jsonl"

    assert (
        main(["--json", "export", "messages", "@chan", "--output", str(destination)])
        == 0
    )

    assert json.loads(capsys.readouterr().out)["export"]["count"] == 10_000
    lines = destination.read_text().splitlines()
    assert len(lines) == 10_000
    assert json.loads(lines[0])["id"] == 1
    assert json.loads(lines[-1])["id"] == 10_000


def test_export_messages_unknown_dialog_exits_4(
    config_env, monkeypatch, tmp_path, capsys
):
    make_session_fake(monkeypatch, make_export_fake())

    assert (
        main(
            [
                "--json",
                "export",
                "messages",
                "@ghost",
                "--output",
                str(tmp_path / "out.jsonl"),
            ]
        )
        == 4
    )
    assert json.loads(capsys.readouterr().err)["error"]["code"] == "NOT_FOUND"


def test_export_messages_preserves_existing_destination_when_iteration_fails(
    config_env, monkeypatch, tmp_path
):
    fake = make_export_fake(messages=[make_message(1, "message")])
    fake.iter_messages_error = OSError("disk failed")
    make_session_fake(monkeypatch, fake)
    destination = tmp_path / "messages.jsonl"
    destination.write_text("previous\n")

    assert main(["export", "messages", "@chan", "--output", str(destination)]) == 1
    assert destination.read_text() == "previous\n"


def test_atomic_export_cleans_up_temporary_file_on_cancellation(tmp_path):
    destination = tmp_path / "messages.jsonl"

    with pytest.raises(asyncio.CancelledError):
        with _atomic_text_destination(destination) as handle:
            handle.write("partial\n")
            raise asyncio.CancelledError

    assert list(tmp_path.glob(".messages.jsonl.*.tmp")) == []


def test_export_subscribers_writes_header_and_quoted_rows(
    config_env, monkeypatch, tmp_path, capsys
):
    fake = make_export_fake(
        participants=[
            ns(
                id=7,
                username="alice",
                first_name="Alice, Jr.",
                last_name=None,
                phone=None,
                bot=False,
            )
        ]
    )
    make_session_fake(monkeypatch, fake)
    destination = tmp_path / "subscribers.csv"

    assert (
        main(["--json", "export", "subscribers", "@chan", "--output", str(destination)])
        == 0
    )

    assert json.loads(capsys.readouterr().out)["export"]["count"] == 1
    assert list(csv.DictReader(destination.open())) == [
        {
            "id": "7",
            "username": "alice",
            "first_name": "Alice, Jr.",
            "last_name": "",
            "phone": "",
            "is_bot": "False",
        }
    ]
    assert fake.iter_participants_calls == [(fake._entities["@chan"], None)]


def test_export_broadcast_subscribers_unions_prefix_searches(
    config_env, monkeypatch, tmp_path, capsys
):
    """Broadcast channels hard-cap getParticipants at one page; prefix-union past it."""
    from telethon.tl import functions

    from tgcli.commands import export as export_mod

    monkeypatch.setattr(export_mod, "_PARTICIPANTS_PAGE", 2)
    monkeypatch.setattr(export_mod, "_SEARCH_REFINE_ALPHABET", "ab")

    def member(user_id, username):
        return ns(
            id=user_id,
            username=username,
            first_name=username,
            last_name=None,
            phone=None,
            bot=False,
        )

    users = {
        1: member(1, "a1"),
        2: member(2, "a2"),
        3: member(3, "b1"),
    }
    entity = ns(id=-1001234, title="Channel", broadcast=True)
    fake = FakeClient(
        entities={"@chan": entity},
        participants_count=3,
        participant_search={
            "": [users[1], users[2]],
            "a": [users[1], users[2]],
            "b": [users[3]],
        },
    )
    make_session_fake(monkeypatch, fake)
    destination = tmp_path / "subscribers.csv"

    assert (
        main(["--json", "export", "subscribers", "@chan", "--output", str(destination)])
        == 0
    )

    assert json.loads(capsys.readouterr().out)["export"]["count"] == 3
    assert {row["id"] for row in csv.DictReader(destination.open())} == {"1", "2", "3"}
    assert fake.iter_participants_calls == []
    assert any(
        isinstance(req, functions.channels.GetParticipantsRequest)
        for req in fake.call_requests
    )


def test_export_subscribers_neutralizes_formula_cells(
    config_env, monkeypatch, tmp_path
):
    fake = make_export_fake(
        participants=[
            ns(
                id=7,
                username="=SUM(1,1)",
                first_name="+cmd",
                last_name="@value",
                phone=None,
                bot=False,
            )
        ]
    )
    make_session_fake(monkeypatch, fake)
    destination = tmp_path / "subscribers.csv"

    assert main(["export", "subscribers", "@chan", "--output", str(destination)]) == 0

    assert list(csv.DictReader(destination.open())) == [
        {
            "id": "7",
            "username": "'=SUM(1,1)",
            "first_name": "'+cmd",
            "last_name": "'@value",
            "phone": "",
            "is_bot": "False",
        }
    ]


def test_export_subscribers_accepts_numeric_dialog_id(
    config_env, monkeypatch, tmp_path
):
    entity = ns(id=-1003890108644, title="mirror")
    fake = FakeClient(
        participants=[
            ns(
                id=7,
                username="alice",
                first_name=None,
                last_name=None,
                phone=None,
                bot=False,
            )
        ],
        entities={-1003890108644: entity},
    )
    make_session_fake(monkeypatch, fake)
    destination = tmp_path / "subscribers.csv"

    assert (
        main(["export", "subscribers", "-1003890108644", "--output", str(destination)])
        == 0
    )

    assert len(list(csv.DictReader(destination.open()))) == 1


def test_export_subscribers_empty_channel_keeps_only_header(
    config_env, monkeypatch, tmp_path
):
    make_session_fake(monkeypatch, make_export_fake())
    destination = tmp_path / "subscribers.csv"

    assert main(["export", "subscribers", "@chan", "--output", str(destination)]) == 0
    assert destination.read_text().splitlines() == [
        "id,username,first_name,last_name,phone,is_bot"
    ]


def test_takeout_delay_is_a_retryable_exit_5(config_env, monkeypatch, tmp_path, capsys):
    fake = make_export_fake()
    fake.takeout_error = telethon_errors.TakeoutInitDelayError(request=None, capture=90)
    make_session_fake(monkeypatch, fake)

    assert (
        main(
            [
                "--json",
                "export",
                "messages",
                "@chan",
                "--output",
                str(tmp_path / "out.jsonl"),
            ]
        )
        == 5
    )
    assert json.loads(capsys.readouterr().err)["error"] == {
        "code": "FLOOD_WAIT",
        "message": "takeout is unavailable for 90s; retry after 90s",
        "retry_after": 90,
    }


def test_export_has_no_default_overall_timeout(config_env, monkeypatch, tmp_path):
    from tgcli import cli

    fake = make_export_fake()
    make_session_fake(monkeypatch, fake)
    observed = []
    original_wait_for = cli.asyncio.wait_for

    async def record_timeout(awaitable, timeout):
        observed.append(timeout)
        return await original_wait_for(awaitable, timeout)

    monkeypatch.setattr(cli.asyncio, "wait_for", record_timeout)

    assert (
        main(["export", "messages", "@chan", "--output", str(tmp_path / "out.jsonl")])
        == 0
    )
    assert observed == [None]
