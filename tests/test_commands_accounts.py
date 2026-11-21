"""Offline account lifecycle: show and remove."""

from __future__ import annotations

import fcntl
import json
from pathlib import Path

import pytest

from tgcli.cli import main
from tgcli.commands import accounts as accounts_cmd
from tgcli.config import load_config
from tgcli.errors import NotFoundError, PolicyError

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
    assert not (env["state"] / "sessions" / "work.session.lock").exists()


def test_show_unknown_alias_exit_4(env, capsys):
    code = main(["accounts", "show", "ghost", "--json"])
    assert code == 4
    err = json.loads(capsys.readouterr().err)
    assert err["error"]["code"] == "NOT_FOUND"


def test_show_unknown_alias_even_with_orphan_session(env):
    _session(env, "ghost")
    with pytest.raises(NotFoundError):
        accounts_cmd.show_account(load_config(), "ghost")


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


def test_remove_refuses_when_a_multiline_string_looks_like_table_headers(env, capsys):
    """Lines inside a multi-line string were read as table headers, so part
    of an unrelated value was dropped silently."""
    env["config"].write_text(
        SAMPLE.replace(
            'session = "main"\n',
            'session = "main"\nnotes = """\n[accounts.work]\nkeep me\n[other]\n"""\n',
        )
    )
    before = env["config"].read_text()
    path = _session(env, "work")

    assert main(["accounts", "remove", "work", "--confirm", "--json"]) == 3

    assert env["config"].read_text() == before
    assert path.exists()


def test_remove_waits_for_a_late_role_lock_instead_of_failing_midway(
    env, monkeypatch, capsys
):
    """A late role whose lock a promote still held made remove exit 2 after
    the config block and sessions were already gone."""
    import threading

    _session(env, "work")
    late = env["state"] / "sessions" / "work@bot.session"
    replace_text = accounts_cmd.atomic.replace_text

    def promote_during_config_write(path, text):
        if Path(path) == env["config"]:
            late.write_bytes(b"live-key")
            handle = late.with_suffix(".lock").open("w")
            fcntl.flock(handle, fcntl.LOCK_EX)
            threading.Timer(0.3, handle.close).start()
        replace_text(path, text)

    monkeypatch.setattr(
        accounts_cmd.atomic, "replace_text", promote_during_config_write
    )

    assert main(["accounts", "remove", "work", "--confirm", "--json"]) == 0

    assert json.loads(capsys.readouterr().out)["roles"] == {"bot": "deleted"}
    assert not late.exists()


def test_remove_keep_session_keeps_role_sessions(env, capsys):
    _session(env, "work")
    role = _session(env, "work@bot")

    assert (
        main(["accounts", "remove", "work", "--confirm", "--keep-session", "--json"])
        == 0
    )

    assert json.loads(capsys.readouterr().out)["roles"] == {"bot": "kept"}
    assert role.exists()


def test_remove_confirm_with_missing_state_tree(env):
    (env["state"] / "sessions").rmdir()
    env["state"].rmdir()

    data = accounts_cmd.remove_account(
        load_config(), "work", confirm=True, keep_session=False
    )

    assert data["session"] == "absent"
    assert data["backup"] == "absent"
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


def test_remove_unknown_alias_exit_4(env, capsys):
    code = main(["accounts", "remove", "ghost", "--confirm", "--json"])
    assert code == 4


def test_a_new_config_directory_is_private(tmp_path):
    """The config holds the api_hash: a fresh ~/.config/tgcli is 0700 under
    the usual umask 022, not 0755."""
    import os

    fresh = tmp_path / "home" / ".config" / "tgcli" / "config.toml"
    previous = os.umask(0o022)
    try:
        accounts_cmd._append_config_block(fresh, "fresh", 1, "h")
    finally:
        os.umask(previous)

    assert fresh.parent.stat().st_mode & 0o777 == 0o700
    assert fresh.stat().st_mode & 0o777 == 0o600
