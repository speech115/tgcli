"""Tests for login attempt state and promotion."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from tgcli import login_state
from tgcli.errors import NotFoundError

NOW = datetime(2026, 7, 24, 12, 0, tzinfo=UTC)


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    return tmp_path


def test_load_phone_absent_returns_none(state):
    record = login_state.create_attempt(
        "main", "phone", api_id=1, api_hash="h", now=NOW
    )
    assert login_state.load_phone(record["login_id"]) is None


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


def test_promote_discards_phone_sidecar(state):
    record = login_state.create_attempt(
        "main", "phone", api_id=1, api_hash="h", now=NOW
    )
    login_id = record["login_id"]
    login_state.save_phone(login_id, "+7999")
    staged = login_state.staged_session_path(login_id)
    staged.write_bytes(b"new")
    dest = state / "sessions" / "main.session"
    dest.parent.mkdir()
    login_state.promote(login_id, dest, keep_backup=True)
    assert login_state.load_phone(login_id) is None
    assert not (state / "logins" / f"{login_id}.phone").exists()


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
