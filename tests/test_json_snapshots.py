"""The JSON every common command prints, pinned as golden files.

These are a net for refactors: a test here fails whenever a command's stdout
shape or exit code moves, intended or not. An intended change regenerates
the file (see tests/fakes/snapshot.py) and the diff shows up in review.
"""

import datetime as dt
import json

import pytest

from tests.conftest import FakeClient, make_session_fake, ns
from tests.fakes.archive import seed_search_corpus
from tests.fakes.snapshot import assert_snapshot
from tgcli.cli import main

CONFIG = """
default_account = "main"

[accounts.main]
api_id = 12345
api_hash = "abcdef0123456789"

[accounts.work]
api_id = 67890
api_hash = "0123456789abcdef"
"""

WHEN = dt.datetime(2026, 7, 6, 10, 0, tzinfo=dt.UTC)


@pytest.fixture(autouse=True)
def config_env(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(CONFIG)
    monkeypatch.setenv("TGCLI_CONFIG", str(path))


def message(message_id, text, *, minutes=0):
    return ns(
        id=message_id,
        date=WHEN + dt.timedelta(minutes=minutes),
        sender_id=111,
        sender=ns(first_name="Alice", last_name=None, username="alice"),
        text=text,
        media=None,
        reply_to_msg_id=None,
    )


def world():
    channel = ns(
        id=-1001234, title="Channel", username="chan", broadcast=True, access_hash=1
    )
    alice = ns(
        id=111,
        first_name="Alice",
        last_name="Smith",
        username="alice",
        bot=False,
        contact=True,
    )
    messages = [message(42, "hello again", minutes=2), message(41, "hello")]
    dialogs = [
        ns(
            id=-1001234,
            name="Channel",
            is_channel=True,
            is_group=False,
            entity=ns(username="chan"),
            unread_count=3,
            date=WHEN,
        ),
        ns(
            id=111,
            name="Alice Smith",
            is_channel=False,
            is_group=False,
            entity=ns(username="alice"),
            unread_count=1,
            dialog=ns(unread_mentions_count=0),
            date=WHEN,
        ),
    ]
    return FakeClient(
        dialogs=dialogs,
        messages=messages,
        entities={"@chan": channel, "@alice": alice},
        search_messages={"hello": messages},
    )


CASES = [
    ("accounts_list", ["accounts", "list"]),
    ("dialogs", ["dialogs"]),
    ("read", ["read", "@chan"]),
    ("search", ["search", "@chan", "hello"]),
    ("message", ["message", "@chan", "42"]),
    ("message_missing", ["message", "@chan", "99"]),
    ("info_channel", ["info", "@chan"]),
    ("info_user", ["info", "@alice"]),
    ("resolve", ["resolve", "@alice"]),
    ("send_preview", ["send", "@alice", "hello", "--preview"]),
    ("usage_error", ["read"]),
]


@pytest.mark.parametrize(("name", "argv"), CASES, ids=[name for name, _ in CASES])
def test_json_output_matches_snapshot(name, argv, monkeypatch, capsys):
    make_session_fake(monkeypatch, world())

    code = main([*argv, "--json"])

    captured = capsys.readouterr()
    stdout = json.loads(captured.out) if captured.out.strip() else None
    assert_snapshot(name, {"exit": code, "stdout": stdout})


ARCHIVE_CASES = [
    ("archive_search", ["archive", "search", "хакатон"]),
    ("archive_status", ["archive", "status"]),
]


@pytest.mark.parametrize(
    ("name", "argv"), ARCHIVE_CASES, ids=[name for name, _ in ARCHIVE_CASES]
)
def test_archive_json_matches_snapshot(name, argv, monkeypatch, capsys):
    seed_search_corpus(monkeypatch)
    capsys.readouterr()

    code = main([*argv, "--json"])

    stdout = json.loads(capsys.readouterr().out)
    assert_snapshot(name, {"exit": code, "stdout": stdout})
