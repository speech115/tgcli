"""Offline-first doctor checks (ADR-0040)."""

import json
import sys
from contextlib import asynccontextmanager

import pytest
import telethon

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
    # Missing session (and .bak) files are fine permission-wise.
    assert report["checks"]["session_perms_ok"] is True
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


def test_doctor_reports_runtime_fingerprint(config_env, capsys):
    _touch_session("main")

    assert main(["doctor", "--account", "main", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)

    assert data["runtime"]["python"] == sys.executable
    assert data["runtime"]["python_version"] == sys.version.split()[0]
    assert data["runtime"]["telethon"] == telethon.__version__


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


def test_doctor_reports_no_repair_when_previews_are_already_tight(config_env, capsys):
    from tgcli import safety

    _touch_session("main")
    safety.create_preview({"kind": "send"})

    assert main(["doctor", "--json"]) == 0
    captured = capsys.readouterr()
    checks = json.loads(captured.out)["accounts"][0]["checks"]
    assert checks["preview_perms_ok"] is True
    assert checks["preview_perms_repaired"] == 0
    assert "preview file" not in captured.err


def test_doctor_readonly_reports_loose_previews_without_touching_them(
    config_env, capsys
):
    """--readonly forbids local state mutation (ADR-0040), repair included."""
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


def test_doctor_env_readonly_reports_loose_previews_without_touching_them(
    config_env, capsys, monkeypatch
):
    """TGCLI_READONLY=1 is the same gate as --readonly (ADR-0040).

    Every other local mutation asks `safety.enforce_local_mutation_allowed`,
    which reads both; a hand-rolled `if readonly` here honoured only the flag.
    """
    monkeypatch.setenv("TGCLI_READONLY", "1")
    _touch_session("main")
    legacy = _loose_preview()

    assert main(["doctor", "--json"]) == 0
    captured = capsys.readouterr()
    report = json.loads(captured.out)
    checks = report["accounts"][0]["checks"]
    assert checks["preview_perms_ok"] is False
    assert checks["preview_perms_repaired"] == 0
    assert legacy.stat().st_mode & 0o777 == 0o644
    assert report["ok"] is False
    assert "TGCLI_READONLY" in captured.err


def test_doctor_survives_a_previews_directory_that_vanishes(config_env, monkeypatch):
    """`store cleanup` may remove the directory between the probe and the scan."""
    from tgcli import safety

    _touch_session("main")
    previews = safety.previews_dir()
    previews.mkdir(parents=True, exist_ok=True)

    def vanish():
        raise FileNotFoundError(previews)

    monkeypatch.setattr(type(previews), "iterdir", lambda self: vanish())

    assert main(["doctor", "--json"]) == 0


def test_governor_degraded_appears_in_plain_failures_column():
    """ADR-0089: True governor_degraded must surface in --plain failures."""
    checks = {
        "session_file": True,
        "lock_free": True,
        "state_writable": True,
        "preview_perms_ok": True,
        "preview_perms_repaired": 0,
        "audit_perms_ok": True,
        "session_perms_ok": True,
        "state_size": 12,
        "governor_degraded": True,
        "governor_cooldowns": {},
        "authorized": None,
    }
    assert doctor_cmd._local_ok(checks) is False
    rows = doctor_cmd.to_rows(
        {
            "accounts": [
                {
                    "alias": "main",
                    "ok": False,
                    "user": None,
                    "checks": checks,
                    "roles": [],
                }
            ]
        }
    )
    assert rows[0][1] == "fail"
    assert rows[0][3] == "governor_degraded"


def test_healthy_governor_degraded_false_is_not_a_plain_failure():
    """Inverted polarity: False means healthy, must not appear in failures."""
    checks = {
        "session_file": True,
        "lock_free": True,
        "state_writable": True,
        "preview_perms_ok": True,
        "preview_perms_repaired": 0,
        "audit_perms_ok": True,
        "session_perms_ok": True,
        "state_size": 12,
        "governor_degraded": False,
        "governor_cooldowns": {},
        "authorized": None,
    }
    assert doctor_cmd._local_ok(checks) is True
    assert doctor_cmd._plain_failure_keys(checks) == []
    rows = doctor_cmd.to_rows(
        {
            "accounts": [
                {
                    "alias": "main",
                    "ok": True,
                    "user": None,
                    "checks": checks,
                    "roles": [],
                }
            ]
        }
    )
    assert rows[0][3] is None


def test_repaired_count_is_never_read_as_a_failed_check():
    """An int in a dict of booleans must not reach `ok` or the failures column."""
    checks = {
        "session_file": True,
        "lock_free": True,
        "state_writable": True,
        "preview_perms_ok": True,
        "preview_perms_repaired": 0,
        "audit_perms_ok": True,
        "session_perms_ok": True,
        "state_size": 12,
        "authorized": None,
    }
    assert doctor_cmd._local_ok(checks) is True
    rows = doctor_cmd.to_rows(
        {"accounts": [{"alias": "main", "ok": True, "user": None, "checks": checks}]}
    )
    assert rows[0][3] is None


def test_doctor_flags_loose_session_file_modes(config_env, capsys):
    """A 0644 .session file is full account access for every local reader."""
    from tgcli import session

    _touch_session("main")
    (session.state_dir() / "sessions" / "main.session").chmod(0o644)

    assert main(["doctor", "--account", "main", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)["accounts"][0]
    assert report["checks"]["session_perms_ok"] is False
    assert report["ok"] is False


def test_doctor_session_perms_cover_the_backup_file(config_env, capsys):
    from tgcli import session

    _touch_session("main")
    bak = session.state_dir() / "sessions" / "main.session.bak"
    bak.write_bytes(b"old")
    bak.chmod(0o644)

    assert main(["doctor", "--account", "main", "--json"]) == 0
    report = json.loads(capsys.readouterr().out)["accounts"][0]
    assert report["checks"]["session_perms_ok"] is False
    assert report["ok"] is False


def test_mode_ok_rejects_group_and_other_bits(tmp_path):
    path = tmp_path / "audit.jsonl"
    path.write_text("")
    path.chmod(0o640)
    assert doctor_cmd._mode_ok(path) is False
    path.chmod(0o620)
    assert doctor_cmd._mode_ok(path) is False
    path.chmod(0o604)
    assert doctor_cmd._mode_ok(path) is False
    path.chmod(0o600)
    assert doctor_cmd._mode_ok(path) is True


def test_doctor_creates_state_tree_private_under_wide_umask(
    config_env, wide_umask, capsys
):
    from tgcli import safety, session

    _touch_session("main")

    assert main(["doctor", "--account", "main", "--json"]) == 0
    assert session.state_dir().stat().st_mode & 0o777 == 0o700
    assert safety.previews_dir().stat().st_mode & 0o777 == 0o700


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
