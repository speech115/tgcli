"""Tests for login attempt state and promotion."""

from __future__ import annotations

import fcntl
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tgcli import login_state
from tgcli.errors import ConfigError, NotFoundError


NOW = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    return tmp_path


def test_create_attempt_mode_and_fields(state):
    record = login_state.create_attempt("main", "qr", api_id=1, api_hash="h", now=NOW)
    assert record["login_id"].startswith("l_")
    assert record["method"] == "qr"
    assert record["phone_code_hash"] is None
    path = state / "logins" / f"{record['login_id']}.json"
    assert path.is_file()
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert oct((state / "logins").stat().st_mode & 0o777) == "0o700"
    # Atomic write: no leftover tempfile beside the attempt.
    assert list((state / "logins").glob(".login-*.tmp")) == []


def test_update_attempt_rewrites_atomically(state):
    record = login_state.create_attempt("main", "qr", api_id=1, api_hash="h", now=NOW)
    updated = login_state.update_attempt(record["login_id"], phone_code_hash="abc")
    assert updated["phone_code_hash"] == "abc"
    path = state / "logins" / f"{record['login_id']}.json"
    assert '"phone_code_hash": "abc"' in path.read_text()
    assert list((state / "logins").glob(".login-*.tmp")) == []


def test_id_validation_rejects_traversal(state):
    with pytest.raises(NotFoundError):
        login_state.load_attempt("../etc/passwd")
    with pytest.raises(NotFoundError):
        login_state.load_attempt("l_abc/../x")
    with pytest.raises(NotFoundError):
        login_state.load_attempt("not-a-login")


def test_expiry_removes_files_and_raises(state):
    record = login_state.create_attempt(
        "main", "phone", api_id=1, api_hash="h", phone="+1", now=NOW
    )
    login_id = record["login_id"]
    staged = login_state.staged_session_path(login_id)
    staged.write_bytes(b"staged")
    with pytest.raises(NotFoundError, match="expired"):
        login_state.load_attempt(login_id, now=NOW + login_state.LOGIN_TTL)
    assert not (state / "logins" / f"{login_id}.json").exists()
    assert not staged.exists()


def test_promote_atomic_when_replace_fails_midway(state, monkeypatch):
    record = login_state.create_attempt("main", "qr", api_id=1, api_hash="h", now=NOW)
    login_id = record["login_id"]
    staged = login_state.staged_session_path(login_id)
    staged.write_bytes(b"new-session")
    dest = state / "sessions" / "main.session"
    dest.parent.mkdir()
    dest.write_bytes(b"old-session")

    calls = {"n": 0}
    real_replace = os.replace

    def flaky(src, dst):
        calls["n"] += 1
        if calls["n"] == 2:
            raise OSError("simulated crash between moves")
        return real_replace(src, dst)

    monkeypatch.setattr(os, "replace", flaky)
    with pytest.raises(OSError, match="simulated"):
        login_state.promote(login_id, dest, keep_backup=True)

    # Destination restored to old content after failed staged move.
    assert dest.exists()
    assert dest.read_bytes() == b"old-session"
    bak = Path(str(dest) + ".bak")
    assert not bak.exists()
    assert staged.exists()  # second replace failed; staged still there


def test_promote_backup_slot_is_single(state):
    record = login_state.create_attempt("main", "qr", api_id=1, api_hash="h", now=NOW)
    login_id = record["login_id"]
    staged = login_state.staged_session_path(login_id)
    staged.write_bytes(b"first")
    dest = state / "sessions" / "main.session"
    dest.parent.mkdir()
    dest.write_bytes(b"original")
    login_state.promote(login_id, dest, keep_backup=True)
    assert dest.read_bytes() == b"first"
    assert Path(str(dest) + ".bak").read_bytes() == b"original"

    record2 = login_state.create_attempt("main", "qr", api_id=1, api_hash="h", now=NOW)
    staged2 = login_state.staged_session_path(record2["login_id"])
    staged2.write_bytes(b"second")
    login_state.promote(record2["login_id"], dest, keep_backup=True)
    assert dest.read_bytes() == b"second"
    assert Path(str(dest) + ".bak").read_bytes() == b"first"
    assert not Path(str(dest) + ".bak.bak").exists()


def test_promote_refuses_when_destination_lock_held(state):
    record = login_state.create_attempt("main", "qr", api_id=1, api_hash="h", now=NOW)
    staged = login_state.staged_session_path(record["login_id"])
    staged.write_bytes(b"new")
    dest = state / "sessions" / "main.session"
    dest.parent.mkdir()
    dest.write_bytes(b"old")
    lock = dest.with_suffix(".lock").open("w")
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(ConfigError, match="busy"):
            login_state.promote(record["login_id"], dest, keep_backup=True)
        assert dest.read_bytes() == b"old"
    finally:
        fcntl.flock(lock, fcntl.LOCK_UN)
        lock.close()


def test_update_and_discard(state):
    record = login_state.create_attempt(
        "main", "phone", api_id=1, api_hash="h", phone="+7999", now=NOW
    )
    updated = login_state.update_attempt(record["login_id"], phone_code_hash="hash123")
    assert updated["phone_code_hash"] == "hash123"
    staged = login_state.staged_session_path(record["login_id"])
    staged.write_bytes(b"x")
    login_state.discard_attempt(record["login_id"])
    assert not (state / "logins" / f"{record['login_id']}.json").exists()
    assert not staged.exists()
