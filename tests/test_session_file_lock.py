from __future__ import annotations

import fcntl

import pytest

from tgcli import session
from tgcli.config import Account
from tgcli.errors import ConfigError, PolicyError

ACCOUNT = Account(alias="t", api_id=1, api_hash="h", session="t")


@pytest.fixture
def state(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    session.ensure_state_dir("sessions")
    return tmp_path


def test_session_file_lock_raises_config_error_when_busy(state):
    path = session.session_path(ACCOUNT)
    path.write_text("x")
    holder = path.with_suffix(".lock").open("w")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(ConfigError, match=r"session 't' is busy"):
            with session.session_file_lock(path):
                pass
    finally:
        fcntl.flock(holder, fcntl.LOCK_UN)
        holder.close()


def test_session_file_lock_can_raise_policy_error_for_mutations(state):
    path = session.session_path(ACCOUNT)
    path.write_text("x")
    holder = path.with_suffix(".lock").open("w")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    try:
        with pytest.raises(PolicyError, match=r"session 't' is busy"):
            with session.session_file_lock(path, busy_error=PolicyError):
                pass
    finally:
        fcntl.flock(holder, fcntl.LOCK_UN)
        holder.close()


def test_session_file_lock_holds_exclusive_lock_while_entered(state):
    path = session.session_path(ACCOUNT)
    path.write_text("x")
    with session.session_file_lock(path) as handle:
        probe = path.with_suffix(".lock").open("w")
        with pytest.raises(BlockingIOError):
            fcntl.flock(probe, fcntl.LOCK_EX | fcntl.LOCK_NB)
        probe.close()
        assert handle is not None
