"""Offline-first doctor checks."""

import json
from contextlib import asynccontextmanager

import pytest

from tgcli import state_paths
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

    path = state_paths.state_dir() / "sessions" / f"{name}.session"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"session")
    path.chmod(0o600)


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
    assert report["checks"]["session_perms_ok"] is True
    assert report["ok"] is True
    assert data["ok"] is True


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
                        "session_perms_ok": True,
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


@pytest.mark.parametrize(
    ("cryptg", "libssl", "expected"),
    [(object(), None, "cryptg"), (None, len, "libssl"), (None, None, "pyaes")],
)
def test_doctor_reports_the_aes_backend(
    config_env, capsys, monkeypatch, cryptg, libssl, expected
):
    """Telethon picks cryptg, then OpenSSL, then pure-Python pyaes (~150x slower
    than cryptg); the report says which one transfers will use."""
    from telethon.crypto import aes

    monkeypatch.setattr(aes, "cryptg", cryptg)
    monkeypatch.setattr(aes.libssl, "decrypt_ige", libssl)
    _touch_session("main")

    assert main(["doctor", "--account", "main", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["runtime"]["aes"] == expected


def _loose_preview():
    from tgcli import safety

    previews = safety.previews_dir()
    previews.mkdir(parents=True, exist_ok=True)
    legacy = previews / "old.json"
    legacy.write_text("{}")
    legacy.chmod(0o644)
    return legacy


def test_doctor_repairs_loose_preview_modes(config_env, capsys):
    """#172: a pre-1.1.2 0644 preview kept doctor red until a reaping command
    was run. Tightening a file doctor already had to stat is the repair."""
    _touch_session("main")
    legacy = _loose_preview()

    assert main(["doctor", "--json"]) == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    checks = report["accounts"][0]["checks"]
    assert checks["preview_perms_ok"] is True
    assert checks["preview_perms_repaired"] == 1
    assert legacy.stat().st_mode & 0o777 == 0o600
    assert report["ok"] is True
    assert "tightened 1 preview file" in captured.err


def test_doctor_readonly_reports_loose_previews_without_touching_them(
    config_env, capsys
):
    """--readonly forbids local state mutation, repair included."""
    _touch_session("main")
    legacy = _loose_preview()

    assert main(["doctor", "--readonly", "--json"]) == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    checks = report["accounts"][0]["checks"]
    assert checks["preview_perms_ok"] is False
    assert checks["preview_perms_repaired"] == 0
    assert legacy.stat().st_mode & 0o777 == 0o644
    assert report["ok"] is False
    assert "--readonly" in captured.err


def test_doctor_reports_with_an_unparseable_kill_switch(
    config_env, capsys, monkeypatch
):
    """A malformed TGCLI_READONLY blocks repairs but not the health report.

    doctor must answer when everything else refuses; the bad value fails
    closed (no repair) and is named on stderr.
    """
    monkeypatch.setenv("TGCLI_READONLY", "maybe")
    _touch_session("main")
    legacy = _loose_preview()

    assert main(["doctor", "--json"]) == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    checks = report["accounts"][0]["checks"]
    assert checks["preview_perms_repaired"] == 0
    assert legacy.stat().st_mode & 0o777 == 0o644
    assert "TGCLI_READONLY" in captured.err


def test_doctor_flags_loose_session_file_modes(config_env, capsys):
    """A 0644 .session file is full account access for every local reader."""

    _touch_session("main")
    (state_paths.state_dir() / "sessions" / "main.session").chmod(0o644)

    assert main(["doctor", "--account", "main", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)["accounts"][0]
    assert report["checks"]["session_perms_ok"] is False
    assert report["ok"] is False
