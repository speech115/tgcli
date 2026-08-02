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


def _touch_session(name, *, user_id: int | None = None):
    from tgcli import session

    path = session.state_dir() / "sessions" / f"{name}.session"
    path.parent.mkdir(parents=True, exist_ok=True)
    if user_id is None:
        path.write_bytes(b"")
    else:
        import sqlite3

        connection = sqlite3.connect(str(path))
        try:
            connection.execute(
                "CREATE TABLE entities (id integer primary key, hash integer not null)"
            )
            connection.execute(
                "INSERT INTO entities (id, hash) VALUES (0, ?)", (user_id,)
            )
            connection.commit()
        finally:
            connection.close()
    path.chmod(0o600)


def _fake_client(monkeypatch):
    from tgcli import session

    @asynccontextmanager
    async def fake_session(account, *, mutation_safe=False, role=None):
        yield DoctorClient()

    monkeypatch.setattr(session, "client", fake_session)


def test_doctor_reports_all_accounts(config_env, monkeypatch, capsys):
    from tgcli import safety, session

    _touch_session("main")
    _fake_client(monkeypatch)

    assert main(["doctor", "--connect", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    by_alias = {item["alias"]: item for item in data["accounts"]}
    assert by_alias["main"]["ok"] is True
    assert by_alias["main"]["user"]["username"] == "me"
    assert by_alias["spare"]["checks"]["session_file"] is False
    assert by_alias["spare"]["ok"] is False
    assert data["ok"] is False
    assert (session.state_dir() / "sessions" / "main.lock").exists()
    assert not (safety.previews_dir() / ".doctor-probe").exists()


def test_doctor_single_account(config_env, monkeypatch, capsys):
    _touch_session("main")
    _fake_client(monkeypatch)

    assert main(["doctor", "--connect", "--account", "main", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert [item["alias"] for item in data["accounts"]] == ["main"]
    assert data["ok"] is True


def test_doctor_reports_config_error_in_payload(config_env, monkeypatch, capsys):
    from tgcli import session

    _touch_session("main")

    @asynccontextmanager
    async def unavailable_session(account, *, mutation_safe=False, role=None):
        raise ConfigError("session 'main' is not authorized")
        yield

    monkeypatch.setattr(session, "client", unavailable_session)

    assert main(["doctor", "--connect", "--account", "main", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    report = data["accounts"][0]
    assert report["checks"]["authorized"] is False
    assert report["checks"]["error"] == "session 'main' is not authorized"
    assert report["ok"] is False
    assert data["ok"] is False


def test_doctor_reports_runtime_error_in_payload(config_env, monkeypatch, capsys):
    from tgcli import session

    _touch_session("main")

    @asynccontextmanager
    async def failing_session(account, *, mutation_safe=False, role=None):
        raise RuntimeError("unexpected transport failure")
        yield

    monkeypatch.setattr(session, "client", failing_session)

    assert main(["doctor", "--connect", "--account", "main", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    report = data["accounts"][0]
    assert report["checks"]["authorized"] is False
    assert report["checks"]["error"] == "unexpected transport failure"
    assert report["ok"] is False
    assert data["ok"] is False


def test_doctor_missing_session_does_not_create_lock(config_env, capsys):
    from tgcli import session

    sessions = session.state_dir() / "sessions"
    sessions.mkdir(parents=True)

    assert main(["doctor", "--account", "spare", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    report = data["accounts"][0]
    assert report["checks"]["session_file"] is False
    assert report["checks"]["lock_free"] is False
    assert report["checks"]["authorized"] is None
    assert not (sessions / "spare.lock").exists()


def test_doctor_reports_active_governor_cooldowns(config_env, monkeypatch, capsys):
    """G4/L5: doctor exits 0 and reports the cooldown with every type armed."""
    from datetime import UTC, datetime, timedelta

    from tgcli.governor.ledger import Ledger

    _touch_session("main", user_id=1)
    _fake_client(monkeypatch)

    with Ledger.open() as ledger:
        ledger.arm_cooldown(
            1, "messages.GetHistoryRequest", datetime.now(UTC) + timedelta(hours=2)
        )

    assert main(["doctor", "--connect", "--account", "main", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    report = data["accounts"][0]
    assert report["ok"] is True
    cooldowns = report["checks"]["governor_cooldowns"]
    assert "messages.GetHistoryRequest" in cooldowns
    assert report["checks"]["governor_degraded"] is False


def test_doctor_works_while_every_gated_type_is_cooling(
    config_env, monkeypatch, capsys
):
    """G4: doctor stays the one command usable precisely when all refuse."""
    from datetime import UTC, datetime, timedelta

    from tgcli.governor.ledger import Ledger

    _touch_session("main", user_id=1)
    _fake_client(monkeypatch)

    with Ledger.open() as ledger:
        for seconds, key in (
            (7200, "messages.GetHistoryRequest"),
            (3600, "messages.GetDialogsRequest"),
            (1800, "messages.SendMessageRequest"),
        ):
            ledger.arm_cooldown(1, key, datetime.now(UTC) + timedelta(seconds=seconds))

    assert main(["doctor", "--connect", "--account", "main", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    report = data["accounts"][0]
    assert report["ok"] is True
    assert len(report["checks"]["governor_cooldowns"]) == 3
