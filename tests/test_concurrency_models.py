"""Formal model-checking tests + real-filesystem replays of past concurrency bugs.

Each model explores 100% of reachable interleavings up to its actor bound via
BFS (`tests/models/concurrency_models.py`), asserts zero invariant violations
on the real design, and proves every past-bug variant FAILS with a concrete
counterexample trace.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from tests.models.concurrency_models import (
    verify_login_promote,
    verify_preview_commit,
)
from tgcli import login_state, safety
from tgcli.errors import ConfigError, PolicyError


def test_login_promote_model_passes_all_interleavings():
    result = verify_login_promote()

    assert result.violations == {}
    assert result.states_explored > 20


@pytest.mark.parametrize(
    ("bug", "expected_invariant"),
    [
        ("no_backup_on_new", "CommittedSessionIntact"),
        ("unlock_before_config", "CommittedSessionIntact"),
        ("cancel_without_recheck", "CancelledNeverCommits"),
    ],
)
def test_login_promote_model_catches_past_bugs(bug, expected_invariant):
    result = verify_login_promote(bug=bug)

    assert expected_invariant in result.violations
    assert result.violations[expected_invariant]


def test_preview_commit_model_passes_all_interleavings():
    result = verify_preview_commit()

    assert result.violations == {}
    assert result.states_explored > 50


@pytest.mark.parametrize(
    ("bug", "expected_invariant"),
    [
        ("no_commit_flock", "AtMostOnceDelivery"),
        ("sticky_expired_pending", "NoStickyPendingAfterExpiry"),
        ("skip_attempted_marker", "RetryRecoversAfterLostReply"),
        ("unpinned_peer", "ExactIntent"),
    ],
)
def test_preview_commit_model_catches_past_bugs(bug, expected_invariant):
    result = verify_preview_commit(bug=bug)

    assert expected_invariant in result.violations
    assert result.violations[expected_invariant]


def test_replay_pr516_promote_rollback_keeps_existing_session_on_disk(
    tmp_path, monkeypatch
):
    """Replay the PR #516 counterexample on real files and fcntl locks."""
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    attempt = login_state.create_attempt(
        "work", "phone", api_id=1, api_hash="h", is_new=True
    )
    staged = login_state.staged_session_path(attempt["login_id"])
    staged.write_bytes(b"p1-session")

    destination = tmp_path / "sessions" / "work.session"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"p2-committed-session")

    def conflict_on_config():
        raise ConfigError("alias 'work' already exists")

    with pytest.raises(ConfigError):
        login_state.promote(
            attempt["login_id"],
            destination,
            keep_backup=True,
            then=conflict_on_config,
        )

    assert destination.read_bytes() == b"p2-committed-session"


def test_replay_pr217_expired_pending_preview_does_not_stay_sticky_pending(
    tmp_path, monkeypatch
):
    """Replay the PR #217 counterexample against real safety.begin_commit."""
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    t0 = datetime(2026, 11, 22, 12, 0, tzinfo=UTC)
    created = safety.create_preview({"kind": "send", "chat": "@a"}, now=t0)
    preview_id = created["preview_id"]

    safety.begin_commit(preview_id, now=t0 + timedelta(seconds=1), expected_kind="send")
    safety.mark_attempted(preview_id)
    safety.release_commit_locks()

    pending = safety.previews_dir() / f"{preview_id}.pending"
    plain = safety.previews_dir() / f"{preview_id}.json"
    assert pending.is_file()

    with pytest.raises(PolicyError, match="expired"):
        safety.begin_commit(
            preview_id, now=t0 + timedelta(minutes=10), expected_kind="send"
        )

    assert not pending.exists()
    assert plain.is_file()
    assert json.loads(plain.read_text())["payload"]["chat"] == "@a"
    assert not safety.was_attempted(preview_id)
