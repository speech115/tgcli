"""Persistent scheduler invariants for ADR-0087."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime, timedelta

import pytest

from tgcli.errors import PolicyError
from tgcli.jobs import store as jobs_store

NOW = datetime(2026, 8, 10, 12, 0, tzinfo=UTC)


@pytest.fixture
def registry(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    conn = jobs_store.connect("main")
    try:
        yield conn
    finally:
        conn.close()


def _add(
    conn,
    key: str,
    *,
    priority: str = "normal",
    max_attempts: int = 3,
):
    return jobs_store.add_job(
        conn,
        key=key,
        kind="archive-transcribe",
        lane="local",
        spec={"max_attempts": max_attempts},
        priority=priority,
        replace=False,
        now=NOW,
    )["job"]


def test_registry_permissions_wal_and_alias_binding(registry):
    path = jobs_store.path_for("main")
    assert path.is_file()
    assert path.parent.stat().st_mode & 0o777 == 0o700
    assert path.stat().st_mode & 0o777 == 0o600
    assert registry.execute("PRAGMA journal_mode").fetchone()[0] == "wal"
    assert jobs_store.read_meta(registry) == {
        "schema_version": 1,
        "account_alias": "main",
        "account_user_id": None,
    }
    with pytest.raises(PolicyError, match="alias"):
        jobs_store.connect("other", path=path)


def test_priority_ages_after_two_skipped_quanta(registry):
    _add(registry, "high", priority="high")
    _add(registry, "low", priority="low")

    first = jobs_store.claim_next(registry, "local", now=NOW)
    assert first["key"] == "high"
    jobs_store.requeue(
        registry,
        first,
        result={"remaining": True},
        reason="remaining",
        now=NOW + timedelta(seconds=1),
    )
    second = jobs_store.claim_next(registry, "local", now=NOW + timedelta(seconds=2))
    assert second["key"] == "high"
    jobs_store.requeue(
        registry,
        second,
        result={"remaining": True},
        reason="remaining",
        now=NOW + timedelta(seconds=3),
    )
    third = jobs_store.claim_next(registry, "local", now=NOW + timedelta(seconds=4))
    assert third["key"] == "low"


def test_crash_recovery_respects_a_durable_cancel_request(registry):
    _add(registry, "resume")
    running = jobs_store.claim_next(registry, "local", now=NOW)
    assert running["state"] == "running"
    jobs_store.cancel_job(registry, "resume", now=NOW + timedelta(seconds=1))

    recovered = jobs_store.recover_running(
        registry, "local", now=NOW + timedelta(seconds=2)
    )
    assert recovered == {"queued": 0, "cancelled": 1}
    assert jobs_store.show_job(registry, "resume")["job"]["state"] == "cancelled"


def test_cancel_request_wins_at_the_quantum_checkpoint(registry):
    _add(registry, "cancel-at-boundary")
    running = jobs_store.claim_next(registry, "local", now=NOW)
    jobs_store.cancel_job(
        registry, "cancel-at-boundary", now=NOW + timedelta(seconds=1)
    )

    final = jobs_store.complete(
        registry,
        running,
        {"remaining": False},
        now=NOW + timedelta(seconds=2),
    )

    assert final["state"] == "cancelled"
    assert final["last_result"] == {"remaining": False}


def test_runtime_failures_back_off_then_fail_and_delay_the_next_generation(
    registry,
):
    _add(registry, "retry")
    first = jobs_store.claim_next(registry, "local", now=NOW)
    first_failed = jobs_store.fail_runtime(
        registry, first, {"code": "RUNTIME"}, now=NOW
    )
    assert first_failed["state"] == "queued"
    assert first_failed["failure_streak"] == 1
    assert first_failed["not_before"] == (NOW + timedelta(minutes=5)).isoformat()
    first_event = jobs_store.show_job(registry, "retry")["events"][-1]
    assert first_event["detail"] == {
        "error": {"code": "RUNTIME"},
        "failure_streak": 1,
        "not_before": (NOW + timedelta(minutes=5)).isoformat(),
        "reason": "runtime_failure",
    }

    second = jobs_store.claim_next(registry, "local", now=NOW + timedelta(minutes=5))
    second_failed = jobs_store.fail_runtime(
        registry, second, {"code": "RUNTIME"}, now=NOW + timedelta(minutes=5)
    )
    assert second_failed["failure_streak"] == 2
    assert second_failed["not_before"] == (NOW + timedelta(minutes=35)).isoformat()

    third = jobs_store.claim_next(registry, "local", now=NOW + timedelta(minutes=35))
    terminal = jobs_store.fail_runtime(
        registry, third, {"code": "RUNTIME"}, now=NOW + timedelta(minutes=35)
    )
    retry_at = NOW + timedelta(hours=2, minutes=35)
    assert terminal["state"] == "failed"
    assert terminal["failure_streak"] == 3
    assert terminal["not_before"] == retry_at.isoformat()
    terminal_event = jobs_store.show_job(registry, "retry")["events"][-1]
    assert terminal_event["detail"] == {
        "error": {
            "code": "RUNTIME",
            "retry_recommended_at": retry_at.isoformat(),
        },
        "failure_streak": 3,
        "not_before": retry_at.isoformat(),
        "reason": "runtime_failure",
    }

    replacement = jobs_store.add_job(
        registry,
        key="retry",
        kind="archive-transcribe",
        lane="local",
        spec={"max_attempts": 3},
        priority="normal",
        replace=False,
        now=NOW + timedelta(minutes=36),
    )["job"]
    assert replacement["generation"] == 2
    assert replacement["not_before"] == retry_at.isoformat()


def test_event_history_keeps_only_newest_200_per_key(registry):
    _add(registry, "bounded")
    for index in range(205):
        jobs_store.record_event(
            registry,
            key="bounded",
            generation=1,
            event_type="test",
            detail={"index": index},
            now=NOW + timedelta(seconds=index),
        )
    events = jobs_store.list_events(registry, "bounded")
    assert len(events) == 200
    assert events[0]["detail"] == {"index": 5}
    assert events[-1]["detail"] == {"index": 204}


def test_lane_lock_is_nonblocking_and_local_is_independent_from_telegram(
    tmp_path, monkeypatch
):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    with jobs_store.lane_lock("main", "local"):
        with pytest.raises(PolicyError, match="already running"):
            with jobs_store.lane_lock("main", "local"):
                pass
        with jobs_store.lane_lock("main", "telegram"):
            pass


def test_connect_refuses_an_unknown_or_newer_schema(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path))
    path = jobs_store.path_for("main", create_parent=True)
    conn = sqlite3.connect(path)
    conn.execute("CREATE TABLE meta (schema_version INTEGER NOT NULL)")
    conn.execute("INSERT INTO meta VALUES (99)")
    conn.commit()
    conn.close()
    with pytest.raises(PolicyError, match="schema"):
        jobs_store.connect("main")


def test_registry_alias_cannot_escape_the_state_directory(tmp_path, monkeypatch):
    monkeypatch.setenv("TGCLI_STATE_DIR", str(tmp_path / "state"))
    with pytest.raises(PolicyError, match="alias"):
        jobs_store.path_for("../escape", create_parent=True)
    with pytest.raises(PolicyError, match="alias"):
        with jobs_store.lane_lock("../escape", "local"):
            pass
    assert not (tmp_path / "escape").exists()
