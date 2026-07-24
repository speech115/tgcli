"""Offline account lifecycle: show and remove (ADR-0042 Slice 1)."""

from __future__ import annotations

import fcntl
import json
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tgcli.cli import main
from tgcli.commands import accounts as accounts_cmd
from tgcli.config import load_config
from tgcli.errors import NotFoundError, PolicyError
from tgcli.safety import audit_path


SAMPLE = """
default_account = "main"

# keep me
top_level = 1

[accounts.main]
api_id = 12345
api_hash = "hash-main"
session = "main"

[accounts.work]
api_id = 67890
api_hash = "hash-work"
session = "work"
"""


@pytest.fixture
def env(tmp_path, monkeypatch):
    config_path = tmp_path / "config.toml"
    config_path.write_text(SAMPLE)
    state = tmp_path / "state"
    state.mkdir()
    (state / "sessions").mkdir()
    monkeypatch.setenv("TGCLI_CONFIG", str(config_path))
    monkeypatch.setenv("TGCLI_STATE_DIR", str(state))
    return {"config": config_path, "state": state}


def _session(env, alias: str, *, content: bytes = b"session-bytes") -> Path:
    path = env["state"] / "sessions" / f"{alias}.session"
    path.write_bytes(content)
    return path


def test_show_in_config_session_present(env):
    path = _session(env, "main")
    path.chmod(0o600)
    mtime = datetime.fromtimestamp(path.stat().st_mtime, tz=UTC).isoformat()

    data = accounts_cmd.show_account(load_config(), "main")

    assert data == {
        "alias": "main",
        "in_config": True,
        "session": str(path),
        "exists": True,
        "bytes": len(b"session-bytes"),
        "modified": mtime,
        "locked": False,
        "backup": None,
        "authorized": None,
    }


def test_show_in_config_session_absent(env):
    data = accounts_cmd.show_account(load_config(), "work")

    assert data["alias"] == "work"
    assert data["in_config"] is True
    assert data["exists"] is False
    assert data["bytes"] is None
    assert data["modified"] is None
    assert data["locked"] is False
    assert data["backup"] is None
    assert data["authorized"] is None
    assert data["session"].endswith("work.session")


def test_show_unknown_alias_exit_4(env, capsys):
    code = main(["accounts", "show", "ghost", "--json"])
    assert code == 4
    err = json.loads(capsys.readouterr().err)
    assert err["error"]["code"] == "NOT_FOUND"


def test_show_unknown_alias_even_with_orphan_session(env):
    _session(env, "ghost")
    with pytest.raises(NotFoundError):
        accounts_cmd.show_account(load_config(), "ghost")


def test_show_reports_bak_when_present(env):
    path = _session(env, "main")
    bak = Path(str(path) + ".bak")
    bak.write_bytes(b"old")

    data = accounts_cmd.show_account(load_config(), "main")

    assert data["backup"] == str(bak)


def test_show_reports_locked_without_stealing(env):
    path = _session(env, "main")
    lock = path.with_suffix(".lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        data = accounts_cmd.show_account(load_config(), "main")
        assert data["locked"] is True
        # Lock still held by us — show must have released any probe.
        with pytest.raises(BlockingIOError):
            probe = path.with_suffix(".lock").open("w")
            try:
                fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
            finally:
                probe.close()
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def test_show_cli_json_and_plain(env, capsys):
    _session(env, "main")
    assert main(["accounts", "show", "main", "--json"]) == 0
    data = json.loads(capsys.readouterr().out)
    assert data["alias"] == "main"
    assert data["authorized"] is None

    assert main(["accounts", "show", "main", "--plain"]) == 0
    out = capsys.readouterr().out
    assert "main\t" in out
    assert "True" in out or "true" in out.lower() or "1" in out


def test_show_imports_no_telethon(env):
    import tgcli.commands.accounts as mod

    assert "telethon" not in dir(mod)
    source = Path(mod.__file__).read_text()
    assert "telethon" not in source
    assert "TelegramClient" not in source


def test_remove_without_confirm_leaves_files_and_exits_2(env, capsys):
    path = _session(env, "work")
    bak = Path(str(path) + ".bak")
    bak.write_bytes(b"bak")

    code = main(["accounts", "remove", "work", "--json"])
    assert code == 2
    assert path.exists()
    assert bak.exists()
    assert "work" in load_config().accounts
    err = capsys.readouterr().err
    assert "--confirm" in err


def test_remove_confirm_deletes_block_and_files(env, capsys):
    path = _session(env, "work")
    bak = Path(str(path) + ".bak")
    bak.write_bytes(b"bak")
    # sibling account session must survive
    main_path = _session(env, "main", content=b"keep-main")

    code = main(["accounts", "remove", "work", "--confirm", "--json"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data == {
        "alias": "work",
        "config": "removed",
        "session": "deleted",
        "backup": "deleted",
    }
    assert not path.exists()
    assert not bak.exists()
    assert main_path.exists()
    config = load_config()
    assert "work" not in config.accounts
    assert "main" in config.accounts
    text = env["config"].read_text()
    assert "[accounts.work]" not in text
    assert "[accounts.main]" in text
    assert "top_level = 1" in text
    assert "# keep me" in text
    assert config.default_account == "main"
    assert oct(env["config"].stat().st_mode & 0o777) == "0o600"


def test_remove_keep_session(env, capsys):
    path = _session(env, "work")
    bak = Path(str(path) + ".bak")
    bak.write_bytes(b"bak")

    code = main(["accounts", "remove", "work", "--confirm", "--keep-session", "--json"])
    assert code == 0
    data = json.loads(capsys.readouterr().out)
    assert data["session"] == "kept"
    assert data["backup"] == "kept"
    assert path.exists()
    assert bak.exists()
    assert "work" not in load_config().accounts


def test_remove_refuses_when_lock_held(env):
    path = _session(env, "work")
    lock = path.with_suffix(".lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(PolicyError, match="busy"):
            accounts_cmd.remove_account(
                load_config(), "work", confirm=True, keep_session=False
            )
        assert path.exists()
        assert "work" in load_config().accounts
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def test_remove_refuses_default_account(env, capsys):
    _session(env, "main")
    code = main(["accounts", "remove", "main", "--confirm", "--json"])
    assert code == 2
    err = capsys.readouterr().err.lower()
    assert "default_account" in err
    assert "main" in load_config().accounts


def test_remove_readonly_blocks_confirm(env, monkeypatch, capsys):
    _session(env, "work")
    monkeypatch.setenv("TGCLI_READONLY", "1")
    code = main(["accounts", "remove", "work", "--confirm", "--json"])
    assert code == 2
    assert "work" in load_config().accounts
    assert json.loads(capsys.readouterr().err)["error"]["code"] == "BLOCKED"


def test_remove_no_send_does_not_block(env, monkeypatch, capsys):
    _session(env, "work")
    monkeypatch.setenv("TGCLI_NO_SEND", "1")
    code = main(["accounts", "remove", "work", "--confirm", "--json"])
    assert code == 0
    assert "work" not in load_config().accounts
    assert json.loads(capsys.readouterr().out)["config"] == "removed"


def test_remove_audit_before_deletion_and_no_secrets(env, monkeypatch):
    path = _session(env, "work")
    written: list[dict] = []
    original = accounts_cmd.safety.append_audit

    def tracking(action, account, details):
        written.append({"action": action, "account": account, **details})
        assert path.exists()  # still present when audit runs
        original(action, account, details)

    monkeypatch.setattr(accounts_cmd.safety, "append_audit", tracking)

    accounts_cmd.remove_account(load_config(), "work", confirm=True, keep_session=False)

    assert written
    record = written[0]
    assert record["action"] == "accounts-remove"
    assert record["account"] == "work"
    assert "hash-work" not in json.dumps(record)
    assert "api_hash" not in record
    line = audit_path().read_text()
    assert "hash-work" not in line
    assert not path.exists()


def test_remove_unknown_alias_exit_4(env, capsys):
    code = main(["accounts", "remove", "ghost", "--confirm", "--json"])
    assert code == 4
