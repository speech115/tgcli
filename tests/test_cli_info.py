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


def test_info_json_projects_channel_without_access_hash(
    config_env, monkeypatch, capsys
):
    entity = ns(
        id=-1001234,
        title="Channel",
        username="chan",
        broadcast=True,
        access_hash=987654321,
    )
    make_session_fake(monkeypatch, FakeClient(entities={"@chan": entity}))

    code = main(["info", "@chan", "--json"])

    assert code == 0
    assert json.loads(capsys.readouterr().out) == {
        "id": -1001234,
        "name": "Channel",
        "kind": "channel",
        "username": "chan",
    }


def test_info_accepts_numeric_dialog_id(config_env, monkeypatch, capsys):
    entity = ns(id=-1001234, title="Channel", username=None, broadcast=True)
    make_session_fake(monkeypatch, FakeClient(entities={-1001234: entity}))

    code = main(["info", "-1001234", "--json"])

    assert code == 0
    assert json.loads(capsys.readouterr().out)["id"] == -1001234


def test_info_json_projects_user_without_access_hash(config_env, monkeypatch, capsys):
    entity = ns(
        id=1234,
        first_name="Alice",
        last_name="Smith",
        username="alice",
        access_hash=987654321,
    )
    make_session_fake(monkeypatch, FakeClient(entities={"@alice": entity}))

    code = main(["info", "@alice", "--json"])

    assert code == 0
    assert json.loads(capsys.readouterr().out) == {
        "id": 1234,
        "name": "Alice Smith",
        "kind": "user",
        "username": "alice",
    }
