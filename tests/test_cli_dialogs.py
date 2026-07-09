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
        date=dt.datetime(2026, 7, 6, 11, 59, tzinfo=dt.timezone.utc),
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
                "last_message_at": "2026-07-06T11:59:00+00:00",
            }
        ]
    }


def test_dialogs_plain_column_order_frozen(config_env, monkeypatch, capsys):
    make_session_fake(monkeypatch, FakeClient(dialogs=[make_dialog()]))
    main(["--plain", "dialogs"])
    assert capsys.readouterr().out == "-1001234\tchannel\tchan\tChannel\t3\n"
