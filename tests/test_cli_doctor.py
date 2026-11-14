import json
from contextlib import asynccontextmanager

import pytest

from tests.conftest import ns
from tgcli import state_paths
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
    path = state_paths.state_dir() / "sessions" / f"{name}.session"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    path.chmod(0o600)


def _fake_client(monkeypatch):
    from tgcli import session

    @asynccontextmanager
    async def fake_session(account, *, mutation_safe=False, role=None):
        yield DoctorClient()

    monkeypatch.setattr(session, "client", fake_session)


def test_doctor_reports_all_accounts(config_env, monkeypatch, capsys):
    from tgcli import safety

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
    assert (state_paths.state_dir() / "sessions" / "main.lock").exists()
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

    sessions = state_paths.state_dir() / "sessions"
    sessions.mkdir(parents=True)

    assert main(["doctor", "--account", "spare", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    report = data["accounts"][0]
    assert report["checks"]["session_file"] is False
    assert report["checks"]["lock_free"] is False
    assert report["checks"]["authorized"] is None
    assert not (sessions / "spare.lock").exists()


def test_a_hand_made_config_is_tightened_to_0600(config_env, tmp_path, capsys):
    """README's `config.toml` created by hand is 0644 and holds the api_hash."""
    config = tmp_path / "config.toml"
    config.chmod(0o644)

    assert main(["doctor", "--json"]) == 0

    report = json.loads(capsys.readouterr().out)["accounts"][0]
    assert config.stat().st_mode & 0o777 == 0o600
    assert report["checks"]["config_perms_ok"] is True


def test_doctor_flags_a_config_it_cannot_tighten(
    config_env, tmp_path, monkeypatch, capsys
):
    import os

    config = tmp_path / "config.toml"
    config.chmod(0o644)
    real_chmod = os.chmod

    def refuse(path, mode, *args, **kwargs):
        if str(path) == str(config):
            raise PermissionError("not the owner")
        return real_chmod(path, mode, *args, **kwargs)

    monkeypatch.setattr(os, "chmod", refuse)

    assert main(["doctor", "--json"]) == 0

    data = json.loads(capsys.readouterr().out)
    report = data["accounts"][0]
    assert report["checks"]["config_perms_ok"] is False
    assert report["ok"] is False
    assert data["ok"] is False


def test_doctor_plain_names_the_failed_checks(config_env, capsys):
    """An account with no session file: the failures column says which checks."""
    code = main(["doctor", "--account", "main", "--plain"])

    [row] = capsys.readouterr().out.splitlines()
    alias, status, _user, failures = row.split("\t")
    assert (code, alias, status) == (0, "main", "fail")
    assert failures.split(", ") == ["session_file", "lock_free"]
