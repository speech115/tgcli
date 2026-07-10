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


def test_message_json_returns_requested_id(config_env, monkeypatch, capsys):
    make_session_fake(monkeypatch, make_fake())

    code = main(["message", "@chan", "42", "--json"])

    assert code == 0
    assert json.loads(capsys.readouterr().out)["message"]["id"] == 42


def test_message_missing_id_exits_4(config_env, monkeypatch, capsys):
    make_session_fake(monkeypatch, make_fake())

    code = main(["message", "@chan", "99", "--json"])

    assert code == 4
    assert json.loads(capsys.readouterr().err)["error"]["code"] == "NOT_FOUND"


def test_message_plain_sanitizes_message_controls(config_env, monkeypatch, capsys):
    fake = make_fake()
    fake._messages[0].text = "a\tb\r\nc"
    make_session_fake(monkeypatch, fake)

    code = main(["message", "@chan", "42", "--plain"])

    assert code == 0
    assert capsys.readouterr().out == "42\t2026-07-06T10:00:00+00:00\tAlice\ta b  c\n"
