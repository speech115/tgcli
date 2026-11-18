import json
from contextlib import asynccontextmanager

import pytest

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


def _touch_session(name):
    path = state_paths.state_dir() / "sessions" / f"{name}.session"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"")
    path.chmod(0o600)


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
