"""Account-scoped clone flood record (ADR-0045)."""

import json
import os
from datetime import UTC, datetime, timedelta

import pytest

from tgcli.clone import flood, state


def test_arm_and_load_roundtrip():
    deadline = datetime.now(UTC) + timedelta(minutes=10)
    flood.arm_cooldown(42, deadline)

    record = flood.load(42)
    assert record["cooldown_until"] == deadline.isoformat()
    assert record["last_peer_created_at"] is None
    assert flood.cooldown_deadline(42) == datetime.fromisoformat(deadline.isoformat())


def test_expired_deadline_reads_as_none():
    past = datetime.now(UTC) - timedelta(seconds=5)
    flood.arm_cooldown(7, past)

    assert flood.cooldown_deadline(7) is None
    assert flood.load(7)["cooldown_until"] == past.isoformat()


def test_absent_file_reads_as_empty_record():
    assert flood.load(999) == {"cooldown_until": None, "last_peer_created_at": None}
    assert flood.cooldown_deadline(999) is None


def test_corrupt_file_reads_as_empty_record():
    path = flood.path_for(11)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ not json")

    assert flood.load(11) == {"cooldown_until": None, "last_peer_created_at": None}
    assert flood.cooldown_deadline(11) is None


def test_record_peer_created_roundtrip():
    at = datetime.now(UTC)
    flood.record_peer_created(3, at)

    record = flood.load(3)
    assert record["last_peer_created_at"] == at.isoformat()
    assert record["cooldown_until"] is None


def test_arm_preserves_peer_created_timestamp():
    at = datetime.now(UTC) - timedelta(hours=1)
    flood.record_peer_created(5, at)
    deadline = datetime.now(UTC) + timedelta(minutes=5)
    flood.arm_cooldown(5, deadline)

    record = flood.load(5)
    assert record["last_peer_created_at"] == at.isoformat()
    assert record["cooldown_until"] == deadline.isoformat()


def test_arm_cooldown_keeps_later_deadline():
    longer = datetime.now(UTC) + timedelta(minutes=30)
    shorter = datetime.now(UTC) + timedelta(minutes=5)
    flood.arm_cooldown(9, longer)
    flood.arm_cooldown(9, shorter)

    assert flood.load(9)["cooldown_until"] == longer.isoformat()
    assert flood.cooldown_deadline(9) == datetime.fromisoformat(longer.isoformat())


def test_failed_atomic_replace_preserves_previous_record(monkeypatch):
    deadline = datetime.now(UTC) + timedelta(minutes=1)
    flood.arm_cooldown(8, deadline)
    path = flood.path_for(8)
    before = path.read_text()

    def fail_replace(*_args, **_kwargs):
        raise OSError("interrupted replace")

    monkeypatch.setattr(os, "replace", fail_replace)
    with pytest.raises(OSError, match="interrupted replace"):
        flood.arm_cooldown(8, datetime.now(UTC) + timedelta(hours=1))

    assert path.read_text() == before
    assert json.loads(before)["cooldown_until"] == deadline.isoformat()


def test_account_flood_file_lives_under_clones_dir():
    flood.arm_cooldown(1, datetime.now(UTC) + timedelta(seconds=30))
    path = flood.path_for(1)
    assert path.parent == state.clones_dir()
    assert path.name == "account-1.json"
    assert path.is_file()
