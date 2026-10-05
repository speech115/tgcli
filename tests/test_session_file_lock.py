from __future__ import annotations

import fcntl
import threading
import time

import pytest

from tgcli import session
from tgcli.config import Account
from tgcli.errors import ConfigError, PolicyError
from tgcli.governor import pacing

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


def _hold(path):
    holder = path.with_suffix(".lock").open("w")
    fcntl.flock(holder, fcntl.LOCK_EX | fcntl.LOCK_NB)
    return holder


def test_a_busy_session_is_waited_for_then_reported(state, capsys):
    path = session.session_path(ACCOUNT)
    holder = _hold(path)
    pacing.reset_runtime()
    started = time.monotonic()
    try:
        with pytest.raises(ConfigError, match=r"session 't' is busy.*after 0.3s"):
            with session.session_file_lock(path, wait=0.3):
                pass
    finally:
        holder.close()
    assert time.monotonic() - started >= 0.3
    assert capsys.readouterr().err.count("waiting up to 0.3s") == 1
    # Waiting is governed sleep: the --timeout hang detector does not count it.
    assert pacing.total_governed_sleep() > 0


def test_a_session_freed_during_the_wait_is_taken(state):
    path = session.session_path(ACCOUNT)
    holder = _hold(path)
    threading.Timer(0.2, holder.close).start()
    with session.session_file_lock(path, wait=5) as handle:
        assert handle is not None


def test_the_wait_never_outlasts_max_runtime(state):
    path = session.session_path(ACCOUNT)
    holder = _hold(path)
    pacing.reset_runtime(cap=0.05)
    started = time.monotonic()
    try:
        with pytest.raises(ConfigError, match="busy"):
            with session.session_file_lock(path, wait=5):
                pass
    finally:
        holder.close()
        pacing.reset_runtime()
    assert time.monotonic() - started < 1
