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


def make_dialog():
    return ns(
        id=-1001234,
        name="Channel",
        is_channel=True,
        is_group=False,
        entity=ns(username="chan"),
        unread_count=3,
        date=dt.datetime(2026, 7, 6, 11, 59, tzinfo=dt.UTC),
    )


def make_user_dialog(*, unread_count: int, mentions: int = 0):
    return ns(
        id=42,
        name="Unread user",
        is_channel=False,
        is_group=False,
        entity=ns(username="user"),
        unread_count=unread_count,
        dialog=ns(unread_mentions_count=mentions),
        date=dt.datetime(2026, 7, 6, 12, 0, tzinfo=dt.UTC),
    )


def make_megagroup_dialog():
    return ns(
        id=-1005678,
        name="Megagroup",
        is_channel=True,
        is_group=True,
        entity=ns(username="mega"),
        unread_count=0,
        date=dt.datetime(2026, 7, 6, 12, 1, tzinfo=dt.UTC),
    )


def test_dialogs_json_matches_contract(config_env, monkeypatch, capsys):
    make_session_fake(monkeypatch, FakeClient(dialogs=[make_dialog()]))
    code = main(["--json", "dialogs"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data == {
        "dialogs": [
            {
                "id": -1001234,
                "name": "Channel",
                "kind": "channel",
                "username": "chan",
                "unread": 3,
                "mentions": 0,
                "last_message_at": "2026-07-06T11:59:00+00:00",
            }
        ]
    }


def test_dialogs_plain_column_order_frozen(config_env, monkeypatch, capsys):
    make_session_fake(monkeypatch, FakeClient(dialogs=[make_dialog()]))
    main(["--plain", "dialogs"])
    assert capsys.readouterr().out == "-1001234\tchannel\tchan\tChannel\t3\t0\n"


def test_dialogs_unread_only_and_kind_filter(config_env, monkeypatch, capsys):
    client = FakeClient(
        dialogs=[
            make_dialog(),
            make_user_dialog(unread_count=0),
            make_user_dialog(unread_count=0, mentions=2),
        ]
    )
    make_session_fake(monkeypatch, client)

    assert main(["dialogs", "--unread-only", "--kind", "user", "--json"]) == 0

    data = json.loads(capsys.readouterr().out)
    assert data["dialogs"] == [
        {
            "id": 42,
            "name": "Unread user",
            "kind": "user",
            "username": "user",
            "unread": 0,
            "mentions": 2,
            "last_message_at": "2026-07-06T12:00:00+00:00",
        }
    ]


def test_dialogs_kind_group_includes_megagroup_but_not_broadcast(
    config_env, monkeypatch, capsys
):
    client = FakeClient(dialogs=[make_dialog(), make_megagroup_dialog()])
    make_session_fake(monkeypatch, client)

    assert main(["dialogs", "--kind", "group", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["dialogs"] == [
        {
            "id": -1005678,
            "name": "Megagroup",
            "kind": "group",
            "username": "mega",
            "unread": 0,
            "mentions": 0,
            "last_message_at": "2026-07-06T12:01:00+00:00",
        }
    ]


def test_dialogs_limit_zero_returns_no_dialogs(config_env, monkeypatch, capsys):
    make_session_fake(monkeypatch, FakeClient(dialogs=[make_dialog()]))

    assert main(["dialogs", "--limit", "0", "--json"]) == 0

    assert json.loads(capsys.readouterr().out) == {"dialogs": []}


def test_dialogs_unread_only_includes_unread_mentions(config_env, monkeypatch, capsys):
    client = FakeClient(dialogs=[make_user_dialog(unread_count=0, mentions=2)])
    make_session_fake(monkeypatch, client)

    assert main(["dialogs", "--unread-only", "--json"]) == 0

    assert json.loads(capsys.readouterr().out) == {
        "dialogs": [
            {
                "id": 42,
                "name": "Unread user",
                "kind": "user",
                "username": "user",
                "unread": 0,
                "mentions": 2,
                "last_message_at": "2026-07-06T12:00:00+00:00",
            }
        ]
    }


HOSTILE_TITLE = "Ch\x1bannel\rX\x08Y\nZ\tW"


def make_hostile_dialog():
    return ns(
        id=-1001234,
        name=HOSTILE_TITLE,
        is_channel=True,
        is_group=False,
        entity=ns(username="chan"),
        unread_count=3,
        date=dt.datetime(2026, 7, 6, 11, 59, tzinfo=dt.UTC),
    )


def test_dialogs_plain_strips_control_characters_from_the_title(
    config_env, monkeypatch, capsys
):
    make_session_fake(monkeypatch, FakeClient(dialogs=[make_hostile_dialog()]))

    assert main(["--plain", "dialogs"]) == 0

    assert capsys.readouterr().out == "-1001234\tchannel\tchan\tChannelXYZW\t3\t0\n"


def test_dialogs_human_strips_control_characters_from_the_title(
    config_env, monkeypatch, capsys
):
    make_session_fake(monkeypatch, FakeClient(dialogs=[make_hostile_dialog()]))

    assert main(["dialogs"]) == 0

    out = capsys.readouterr().out
    assert out == "-1001234 | channel | chan | ChannelXYZW | 3 | 0\n"


def test_dialogs_json_passes_control_characters_through(
    config_env, monkeypatch, capsys
):
    make_session_fake(monkeypatch, FakeClient(dialogs=[make_hostile_dialog()]))

    assert main(["--json", "dialogs"]) == 0

    data = json.loads(capsys.readouterr().out)
    assert data["dialogs"][0]["name"] == HOSTILE_TITLE
