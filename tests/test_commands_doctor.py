"""Offline-first doctor checks (ADR-0040)."""

import json
from contextlib import asynccontextmanager

import pytest

from tgcli.cli import main
from tgcli.commands import doctor as doctor_cmd
from tgcli.config import load_config


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
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path / "state"))


def _touch_session(name: str) -> None:
    from tgcli import session

    path = session.state_dir() / "sessions" / f"{name}.session"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"session")


def test_doctor_offline_does_not_open_client(config_env, monkeypatch, capsys):
    from tgcli import session

    _touch_session("main")

    @asynccontextmanager
    async def boom(account):
        raise AssertionError("offline doctor must not open session.client")
        yield

    monkeypatch.setattr(session, "client", boom)

    assert main(["doctor", "--account", "main", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    report = data["accounts"][0]
    assert report["checks"]["authorized"] is None
    assert report["checks"]["session_file"] is True
    assert report["checks"]["state_writable"] is True
    assert "state_size" in report["checks"]
    assert "preview_perms_ok" in report["checks"]
    assert "audit_perms_ok" in report["checks"]
    assert report["ok"] is True
    assert data["ok"] is True


def test_doctor_offline_with_missing_session_still_reports_local(
    config_env, monkeypatch, capsys
):
    from tgcli import session

    @asynccontextmanager
    async def boom(account):
        raise AssertionError("offline doctor must not open session.client")
        yield

    monkeypatch.setattr(session, "client", boom)

    assert main(["doctor", "--account", "main", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    report = data["accounts"][0]
    assert report["checks"]["session_file"] is False
    assert report["checks"]["authorized"] is None
    assert report["ok"] is False
    assert "state_size" in report["checks"]


def test_doctor_to_rows_marks_authorized_unknown():
    rows = doctor_cmd.to_rows(
        {
            "accounts": [
                {
                    "alias": "main",
                    "ok": True,
                    "user": None,
                    "checks": {
                        "session_file": True,
                        "lock_free": True,
                        "state_writable": True,
                        "preview_perms_ok": True,
                        "audit_perms_ok": True,
                        "state_size": 12,
                        "authorized": None,
                    },
                }
            ],
            "ok": True,
        }
    )
    assert rows[0][1] == "unknown"


def test_doctor_run_connect_false_skips_client(config_env, monkeypatch):
    from tgcli import session

    _touch_session("main")

    @asynccontextmanager
    async def boom(account):
        raise AssertionError("should not connect")
        yield

    monkeypatch.setattr(session, "client", boom)
    config = load_config()
    data = __import__("asyncio").run(doctor_cmd.run(config, "main", connect=False))
    assert data["accounts"][0]["checks"]["authorized"] is None


def test_doctor_hints_the_remedy_for_loose_preview_modes(config_env, capsys):
    """A legacy 0644 preview fails doctor; the fix must not be a guessing game."""
    from tgcli import safety

    _touch_session("main")
    previews = safety.previews_dir()
    previews.mkdir(parents=True, exist_ok=True)
    legacy = previews / "old.json"
    legacy.write_text("{}")
    legacy.chmod(0o644)

    assert main(["doctor", "--json"]) == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    assert report["accounts"][0]["checks"]["preview_perms_ok"] is False
    assert report["ok"] is False
    assert "tg store cleanup --confirm" in captured.err


def test_doctor_flags_unprobeable_lock_as_not_free(config_env, capsys):
    """A lock path that cannot be opened is unhealthy, not silently free."""
    from tgcli import session

    _touch_session("main")
    (session.state_dir() / "sessions" / "main.lock").mkdir()

    code = main(["doctor", "--json"])

    report = json.loads(capsys.readouterr().out)["accounts"][0]
    assert report["checks"]["lock_free"] is False
    assert report["ok"] is False
    assert code == 0
