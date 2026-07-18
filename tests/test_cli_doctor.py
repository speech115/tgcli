import json
from contextlib import asynccontextmanager

import pytest

from tests.conftest import ns
from tgcli.cli import main
from tgcli.errors import ConfigError


SAMPLE = """
default_account = "main"

[accounts.main]
api_id = 12345
api_hash = "abcdef0123456789"

[accounts.spare]
api_id = 12345
api_hash = "abcdef0123456789"
"""


@pytest.fixture
def config_env(tmp_path, monkeypatch):
    path = tmp_path / "config.toml"
    path.write_text(SAMPLE)
    monkeypatch.setenv("TGCLI_CONFIG", str(path))


class DoctorClient:
    async def get_me(self):
        return ns(id=1, username="me", first_name="Me")


def _touch_session(name):
    from tgcli import session

    path = session.state_dir() / "sessions" / f"{name}.session"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")


def _fake_client(monkeypatch):
    from tgcli import session

    @asynccontextmanager
    async def fake_session(account):
        yield DoctorClient()

    monkeypatch.setattr(session, "client", fake_session)


def test_doctor_reports_all_accounts(config_env, monkeypatch, capsys):
    _touch_session("main")
    _fake_client(monkeypatch)

    assert main(["doctor", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    by_alias = {item["alias"]: item for item in data["accounts"]}
    assert by_alias["main"]["ok"] is True
    assert by_alias["main"]["user"]["username"] == "me"
    assert by_alias["spare"]["checks"]["session_file"] is False
    assert by_alias["spare"]["ok"] is False
    assert data["ok"] is False


def test_doctor_single_account(config_env, monkeypatch, capsys):
    _touch_session("main")
    _fake_client(monkeypatch)

    assert main(["doctor", "--account", "main", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [item["alias"] for item in data["accounts"]] == ["main"]
    assert data["ok"] is True


def test_doctor_reports_config_error_in_payload(config_env, monkeypatch, capsys):
    from tgcli import session

    _touch_session("main")

    @asynccontextmanager
    async def unavailable_session(account):
        raise ConfigError("session 'main' is not authorized")
        yield

    monkeypatch.setattr(session, "client", unavailable_session)

    assert main(["doctor", "--account", "main", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    report = data["accounts"][0]
    assert report["checks"]["authorized"] is False
    assert report["checks"]["error"] == "session 'main' is not authorized"
    assert report["ok"] is False
    assert data["ok"] is False
