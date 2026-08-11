"""Tests for login attempt state and promotion."""

from __future__ import annotations

import fcntl
import json
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
    record = login_state.create_attempt(
        "main", "phone", api_id=1, api_hash="h", now=NOW
    )
    assert record["login_id"].startswith("l_")
    assert record["method"] == "phone"
    assert record["phone_code_hash"] is None
    path = state / "logins" / f"{record['login_id']}.json"
    assert path.is_file()
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert oct((state / "logins").stat().st_mode & 0o777) == "0o700"
    # Atomic write: no leftover tempfile beside the attempt.
    assert list((state / "logins").glob(".login-*.tmp")) == []


def test_update_attempt_rewrites_atomically(state):
    record = login_state.create_attempt(
        "main", "phone", api_id=1, api_hash="h", now=NOW
    )
    updated = login_state.update_attempt(
        record["login_id"], phone_code_hash="abc", now=NOW
    )
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


def test_expiry_raises_without_deleting_staged_session(state):
    """A read reports expiry; it never destroys in-flight login material.

    A forward clock step during a multi-step login used to make
    `load_attempt` discard the attempt json *and* the staged session, so
    merely inspecting an attempt threw away what the user was creating.
    """
    record = login_state.create_attempt(
        "main", "phone", api_id=1, api_hash="h", phone="+1", now=NOW
    )
    login_id = record["login_id"]
    staged = login_state.staged_session_path(login_id)
    staged.write_bytes(b"staged")
    attempt_json = state / "logins" / f"{login_id}.json"

    with pytest.raises(NotFoundError, match="expired"):
        login_state.load_attempt(login_id, now=NOW + login_state.LOGIN_TTL)

    assert attempt_json.exists()
    assert staged.exists()

    # Deleting stays with the explicit paths (`store cleanup`, discard).
    login_state.discard_attempt(login_id)
    assert not attempt_json.exists()
    assert not staged.exists()


def test_promote_atomic_when_replace_fails_midway(state, monkeypatch):
    record = login_state.create_attempt(
        "main", "phone", api_id=1, api_hash="h", now=NOW
    )
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
    record = login_state.create_attempt(
        "main", "phone", api_id=1, api_hash="h", now=NOW
    )
    login_id = record["login_id"]
    staged = login_state.staged_session_path(login_id)
    staged.write_bytes(b"first")
    dest = state / "sessions" / "main.session"
    dest.parent.mkdir()
    dest.write_bytes(b"original")
    login_state.promote(login_id, dest, keep_backup=True)
    assert dest.read_bytes() == b"first"
    assert Path(str(dest) + ".bak").read_bytes() == b"original"

    record2 = login_state.create_attempt(
        "main", "phone", api_id=1, api_hash="h", now=NOW
    )
    staged2 = login_state.staged_session_path(record2["login_id"])
    staged2.write_bytes(b"second")
    login_state.promote(record2["login_id"], dest, keep_backup=True)
    assert dest.read_bytes() == b"second"
    assert Path(str(dest) + ".bak").read_bytes() == b"first"
    assert not Path(str(dest) + ".bak.bak").exists()


def test_promote_enforces_sessions_dir_0700_and_files_0600(state, wide_umask):
    """The promoted .session (and its .bak) are account secrets; the mirror
    of the session.client() tighten must hold on the login path too."""
    record = login_state.create_attempt(
        "main", "phone", api_id=1, api_hash="h", now=NOW
    )
    login_id = record["login_id"]
    staged = login_state.staged_session_path(login_id)
    staged.write_bytes(b"new")
    os.chmod(staged, 0o644)
    dest = state / "sessions" / "main.session"
    dest.parent.mkdir()
    os.chmod(dest.parent, 0o777)
    dest.write_bytes(b"old")
    os.chmod(dest, 0o644)

    login_state.promote(login_id, dest, keep_backup=True)

    assert dest.parent.stat().st_mode & 0o777 == 0o700
    assert dest.stat().st_mode & 0o777 == 0o600
    assert Path(str(dest) + ".bak").stat().st_mode & 0o777 == 0o600


def test_promote_refuses_when_destination_lock_held(state):
    record = login_state.create_attempt(
        "main", "phone", api_id=1, api_hash="h", now=NOW
    )
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
    updated = login_state.update_attempt(
        record["login_id"], phone_code_hash="hash123", now=NOW
    )
    assert updated["phone_code_hash"] == "hash123"
    staged = login_state.staged_session_path(record["login_id"])
    staged.write_bytes(b"x")
    login_state.discard_attempt(record["login_id"])
    assert not (state / "logins" / f"{record['login_id']}.json").exists()
    assert not staged.exists()


def test_load_attempt_rejects_a_naive_expiry(tmp_path, monkeypatch):
    """Same class as the store fix: a naive stamp must not raise TypeError."""
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    record = login_state.create_attempt("main", "phone", api_id=1, api_hash="h")
    path = login_state.logins_dir() / f"{record['login_id']}.json"
    stored = json.loads(path.read_text())
    stored["expires_at"] = "2026-07-26T12:00:00"
    path.write_text(json.dumps(stored))

    with pytest.raises(NotFoundError):
        login_state.load_attempt(record["login_id"])
