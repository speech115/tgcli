"""Account-scoped job CRUD, state machine, and lane locks (ADR-0087)."""

from __future__ import annotations

import fcntl
import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from tgcli.errors import NotFoundError, PolicyError
from tgcli.jobs import db, model
from tgcli.session import restrict_file

bind_user = db.bind_user
connect = db.connect
connect_existing = db.connect_existing
connect_mutating = db.connect_mutating
inventory = db.inventory
path_for = db.path_for
read_meta = db.read_meta


def _stamp(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).isoformat(timespec="seconds")


def _decode(raw: str | None) -> Any:
    return None if raw is None else json.loads(raw)


def _job(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "key": str(row["key"]),
        "generation": int(row["generation"]),
        "kind": str(row["kind"]),
        "lane": str(row["lane"]),
        "spec": _decode(row["spec_json"]),
        "priority": model.PRIORITY_NAMES[int(row["priority"])],
        "state": str(row["state"]),
        "created_at": str(row["created_at"]),
        "updated_at": str(row["updated_at"]),
        "started_at": row["started_at"],
        "finished_at": row["finished_at"],
        "not_before": row["not_before"],
        "last_run_at": row["last_run_at"],
        "failure_streak": int(row["failure_streak"]),
        "skipped_quanta": int(row["skipped_quanta"]),
        "cancel_requested_at": row["cancel_requested_at"],
        "last_result": _decode(row["last_result_json"]),
        "last_error": _decode(row["last_error_json"]),
        "quantum_count": int(row["quantum_count"]),
    }


def _latest_row(conn: sqlite3.Connection, key: str) -> sqlite3.Row | None:
    return conn.execute(
        "SELECT * FROM jobs WHERE key = ? ORDER BY generation DESC LIMIT 1", (key,)
    ).fetchone()


def _event(
    conn: sqlite3.Connection,
    *,
    key: str,
    generation: int,
    event_type: str,
    detail: dict,
    timestamp: str,
) -> None:
    conn.execute(
        "INSERT INTO events(key, generation, timestamp, type, detail_json) "
        "VALUES (?, ?, ?, ?, ?)",
        (key, generation, timestamp, event_type, model.canonical_json(detail)),
    )
    conn.execute(
        "DELETE FROM events WHERE key = ? AND id NOT IN "
        "(SELECT id FROM events WHERE key = ? ORDER BY id DESC LIMIT ?)",
        (key, key, model.EVENT_LIMIT),
    )


def record_event(
    conn: sqlite3.Connection,
    *,
    key: str,
    generation: int,
    event_type: str,
    detail: dict,
    now: datetime | None = None,
) -> None:
    with conn:
        _event(
            conn,
            key=key,
            generation=generation,
            event_type=event_type,
            detail=detail,
            timestamp=_stamp(now),
        )


def list_events(conn: sqlite3.Connection, key: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT generation, timestamp, type, detail_json FROM events "
        "WHERE key = ? ORDER BY id",
        (key,),
    ).fetchall()
    return [
        {
            "generation": int(row["generation"]),
            "timestamp": str(row["timestamp"]),
            "type": str(row["type"]),
            "detail": _decode(row["detail_json"]),
        }
        for row in rows
    ]


def add_job(
    conn: sqlite3.Connection,
    *,
    key: str,
    kind: str,
    lane: str,
    spec: dict,
    priority: str,
    replace: bool,
    now: datetime | None = None,
) -> dict[str, Any]:
    key = model.validate_key(key)
    priority = model.validate_priority(priority)
    if lane not in model.LANES:
        raise PolicyError(f"unknown jobs lane: {lane}")
    digest = model.spec_hash(kind, spec, priority)
    timestamp = _stamp(now)
    conn.execute("BEGIN IMMEDIATE")
    try:
        latest = _latest_row(conn, key)
        if latest is not None:
            same = str(latest["spec_hash"]) == digest
            state = str(latest["state"])
            if state in ("queued", "running") and same:
                conn.commit()
                return {"job": _job(latest), "created": False, "noop": True}
            if not same and not replace:
                raise PolicyError(
                    f"job {key!r} already has a different spec; pass --replace"
                )
            if state == "running":
                raise PolicyError(f"job {key!r} is running; cancel it before --replace")
            if state == "queued":
                conn.execute(
                    "UPDATE jobs SET state = 'cancelled', updated_at = ?, "
                    "finished_at = ? WHERE key = ? AND generation = ?",
                    (timestamp, timestamp, key, int(latest["generation"])),
                )
                _event(
                    conn,
                    key=key,
                    generation=int(latest["generation"]),
                    event_type="replaced",
                    detail={},
                    timestamp=timestamp,
                )
            generation = int(latest["generation"]) + 1
            not_before = latest["not_before"] if state == "failed" and same else None
        else:
            generation = 1
            not_before = None
        conn.execute(
            "INSERT INTO jobs(key, generation, kind, lane, spec_json, spec_hash, "
            "priority, state, created_at, updated_at, not_before) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)",
            (
                key,
                generation,
                kind,
                lane,
                model.canonical_json(spec),
                digest,
                model.PRIORITY_VALUES[priority],
                timestamp,
                timestamp,
                not_before,
            ),
        )
        _event(
            conn,
            key=key,
            generation=generation,
            event_type="created",
            detail={"kind": kind, "lane": lane, "priority": priority},
            timestamp=timestamp,
        )
        row = _latest_row(conn, key)
        conn.commit()
        assert row is not None
        return {"job": _job(row), "created": True, "noop": False}
    except Exception:
        conn.rollback()
        raise


def list_jobs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT j.* FROM jobs j JOIN "
        "(SELECT key, MAX(generation) generation FROM jobs GROUP BY key) latest "
        "ON latest.key = j.key AND latest.generation = j.generation "
        "ORDER BY j.key"
    ).fetchall()
    return [_job(row) for row in rows]


def show_job(conn: sqlite3.Connection, key: str) -> dict[str, Any]:
    model.validate_key(key)
    row = _latest_row(conn, key)
    if row is None:
        raise NotFoundError(f"unknown job key: {key!r}")
    return {"job": _job(row), "events": list_events(conn, key)}


def rearm_job(
    conn: sqlite3.Connection,
    key: str,
    *,
    expected_lane: str,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Create the next recurring generation after a completed or failed one.

    Runs under the lane lock: a running latest row is stale and the lane run
    recovers it, so it is left alone like a queued or cancelled one. A failed
    generation is retried two hours after it failed.
    """
    model.validate_key(key)
    timestamp = _stamp(now)
    conn.execute("BEGIN IMMEDIATE")
    try:
        latest = _latest_row(conn, key)
        if latest is None:
            raise NotFoundError(f"unknown job key: {key!r}")
        lane = str(latest["lane"])
        if lane != expected_lane:
            raise PolicyError(
                f"job {key!r} lane changed from {expected_lane!r} to {lane!r}; retry"
            )
        state = str(latest["state"])
        if state in ("queued", "running", "cancelled"):
            conn.commit()
            return {"job": _job(latest), "created": False, "noop": True}
        not_before = None
        if state == "failed":
            failed_at = datetime.fromisoformat(str(latest["updated_at"]))
            not_before = _stamp(failed_at + model.RUNTIME_TERMINAL_NOT_BEFORE)
        generation = int(latest["generation"]) + 1
        conn.execute(
            "INSERT INTO jobs(key, generation, kind, lane, spec_json, spec_hash, "
            "priority, state, created_at, updated_at, not_before) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, 'queued', ?, ?, ?)",
            (
                key,
                generation,
                str(latest["kind"]),
                lane,
                str(latest["spec_json"]),
                str(latest["spec_hash"]),
                int(latest["priority"]),
                timestamp,
                timestamp,
                not_before,
            ),
        )
        _event(
            conn,
            key=key,
            generation=generation,
            event_type="rearmed",
            detail={"previous_generation": int(latest["generation"])},
            timestamp=timestamp,
        )
        row = _latest_row(conn, key)
        conn.commit()
        assert row is not None
        return {"job": _job(row), "created": True, "noop": False}
    except Exception:
        conn.rollback()
        raise


def cancel_job(
    conn: sqlite3.Connection, key: str, *, now: datetime | None = None
) -> dict[str, Any]:
    model.validate_key(key)
    timestamp = _stamp(now)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = _latest_row(conn, key)
        if row is None:
            raise NotFoundError(f"unknown job key: {key!r}")
        state = str(row["state"])
        requested = False
        if state == "queued":
            conn.execute(
                "UPDATE jobs SET state = 'cancelled', updated_at = ?, "
                "finished_at = ? WHERE key = ? AND generation = ?",
                (timestamp, timestamp, key, int(row["generation"])),
            )
            event_type = "cancelled"
        elif state == "running":
            conn.execute(
                "UPDATE jobs SET cancel_requested_at = ?, updated_at = ? "
                "WHERE key = ? AND generation = ?",
                (timestamp, timestamp, key, int(row["generation"])),
            )
            event_type = "cancel_requested"
            requested = True
        else:
            conn.commit()
            return {"job": _job(row), "cancel_requested": False, "noop": True}
        _event(
            conn,
            key=key,
            generation=int(row["generation"]),
            event_type=event_type,
            detail={},
            timestamp=timestamp,
        )
        updated = _latest_row(conn, key)
        conn.commit()
        assert updated is not None
        return {"job": _job(updated), "cancel_requested": requested, "noop": False}
    except Exception:
        conn.rollback()
        raise


def _eligible_sql() -> str:
    return (
        "state = 'queued' AND cancel_requested_at IS NULL AND "
        "(not_before IS NULL OR not_before <= ?)"
    )


def claim_next(
    conn: sqlite3.Connection, lane: str, *, now: datetime | None = None
) -> dict[str, Any] | None:
    if lane not in model.LANES:
        raise PolicyError(f"unknown jobs lane: {lane}")
    timestamp = _stamp(now)
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT * FROM jobs WHERE lane = ? AND "
            + _eligible_sql()
            + " ORDER BY (priority + MIN(skipped_quanta, 2)) DESC, "
            "CASE WHEN last_run_at IS NULL THEN 0 ELSE 1 END, last_run_at, key LIMIT 1",
            (lane, timestamp),
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        conn.execute(
            "UPDATE jobs SET skipped_quanta = skipped_quanta + 1, updated_at = ? "
            "WHERE lane = ? AND "
            + _eligible_sql()
            + " AND NOT (key = ? AND generation = ?)",
            (timestamp, lane, timestamp, str(row["key"]), int(row["generation"])),
        )
        conn.execute(
            "UPDATE jobs SET state = 'running', started_at = ?, updated_at = ?, "
            "last_run_at = ?, skipped_quanta = 0, quantum_count = quantum_count + 1 "
            "WHERE key = ? AND generation = ?",
            (
                timestamp,
                timestamp,
                timestamp,
                str(row["key"]),
                int(row["generation"]),
            ),
        )
        _event(
            conn,
            key=str(row["key"]),
            generation=int(row["generation"]),
            event_type="started",
            detail={},
            timestamp=timestamp,
        )
        updated = _latest_row(conn, str(row["key"]))
        conn.commit()
        assert updated is not None
        return _job(updated)
    except Exception:
        conn.rollback()
        raise


def _transition(
    conn: sqlite3.Connection,
    job: dict[str, Any],
    *,
    state: str,
    result: dict | None,
    error: dict | None,
    reason: str,
    not_before: str | None,
    failure_streak: int,
    now: datetime | None,
) -> dict[str, Any]:
    timestamp = _stamp(now)
    conn.execute("BEGIN IMMEDIATE")
    try:
        current = conn.execute(
            "SELECT state, cancel_requested_at FROM jobs "
            "WHERE key = ? AND generation = ?",
            (job["key"], job["generation"]),
        ).fetchone()
        if current is None or str(current["state"]) != "running":
            raise PolicyError(
                f"job {job['key']!r} generation {job['generation']} is not running"
            )
        if current["cancel_requested_at"] is not None and state != "cancelled":
            state = "cancelled"
            error = None
            reason = "cancel_requested"
            not_before = None
            failure_streak = 0
        finished = timestamp if state in ("completed", "failed", "cancelled") else None
        conn.execute(
            "UPDATE jobs SET state = ?, updated_at = ?, finished_at = ?, "
            "not_before = ?, failure_streak = ?, cancel_requested_at = NULL, "
            "last_result_json = ?, last_error_json = ? "
            "WHERE key = ? AND generation = ? AND state = 'running'",
            (
                state,
                timestamp,
                finished,
                not_before,
                failure_streak,
                None if result is None else model.canonical_json(result),
                None if error is None else model.canonical_json(error),
                job["key"],
                job["generation"],
            ),
        )
        detail: dict[str, Any] = {
            "failure_streak": failure_streak,
            "reason": reason,
        }
        if result is not None:
            detail["result"] = result
        if error is not None:
            detail["error"] = error
        if not_before is not None:
            detail["not_before"] = not_before
        _event(
            conn,
            key=job["key"],
            generation=job["generation"],
            event_type=state,
            detail=detail,
            timestamp=timestamp,
        )
        row = _latest_row(conn, job["key"])
        conn.commit()
        assert row is not None
        return _job(row)
    except Exception:
        conn.rollback()
        raise


def requeue(
    conn: sqlite3.Connection,
    job: dict[str, Any],
    *,
    result: dict,
    reason: str,
    now: datetime | None = None,
    not_before: datetime | None = None,
) -> dict[str, Any]:
    return _transition(
        conn,
        job,
        state="queued",
        result=result,
        error=None,
        reason=reason,
        not_before=None if not_before is None else _stamp(not_before),
        failure_streak=0,
        now=now,
    )


def complete(
    conn: sqlite3.Connection,
    job: dict[str, Any],
    result: dict,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    return _transition(
        conn,
        job,
        state="completed",
        result=result,
        error=None,
        reason="remaining_false",
        not_before=None,
        failure_streak=0,
        now=now,
    )


def finish_cancelled(
    conn: sqlite3.Connection,
    job: dict[str, Any],
    result: dict | None,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    return _transition(
        conn,
        job,
        state="cancelled",
        result=result,
        error=None,
        reason="cancel_requested",
        not_before=None,
        failure_streak=0,
        now=now,
    )


def cancel_requested(conn: sqlite3.Connection, job: dict[str, Any]) -> bool:
    row = conn.execute(
        "SELECT cancel_requested_at FROM jobs WHERE key = ? AND generation = ?",
        (job["key"], job["generation"]),
    ).fetchone()
    return row is not None and row["cancel_requested_at"] is not None


def fail_runtime(
    conn: sqlite3.Connection,
    job: dict[str, Any],
    error: dict,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    moment = now or datetime.now(UTC)
    streak = int(job["failure_streak"]) + 1
    if streak >= model.RUNTIME_FAILURE_TERMINAL_STREAK:
        retry_at = moment + model.RUNTIME_TERMINAL_NOT_BEFORE
        return _transition(
            conn,
            job,
            state="failed",
            result=None,
            error={**error, "retry_recommended_at": _stamp(retry_at)},
            reason="runtime_failure",
            not_before=_stamp(retry_at),
            failure_streak=streak,
            now=moment,
        )
    delay = (
        model.RUNTIME_RETRY_DELAY_FIRST
        if streak == 1
        else model.RUNTIME_RETRY_DELAY_SECOND
    )
    return _transition(
        conn,
        job,
        state="queued",
        result=None,
        error=error,
        reason="runtime_failure",
        not_before=_stamp(moment + delay),
        failure_streak=streak,
        now=moment,
    )


def fail_terminal(
    conn: sqlite3.Connection,
    job: dict[str, Any],
    error: dict,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    return _transition(
        conn,
        job,
        state="failed",
        result=None,
        error=error,
        reason="terminal_error",
        not_before=None,
        failure_streak=int(job["failure_streak"]),
        now=now,
    )


def recover_running(
    conn: sqlite3.Connection, lane: str, *, now: datetime | None = None
) -> dict[str, int]:
    timestamp = _stamp(now)
    conn.execute("BEGIN IMMEDIATE")
    queued = 0
    cancelled = 0
    try:
        rows = conn.execute(
            "SELECT * FROM jobs WHERE lane = ? AND state = 'running' ORDER BY key",
            (lane,),
        ).fetchall()
        for row in rows:
            requested = row["cancel_requested_at"] is not None
            state = "cancelled" if requested else "queued"
            conn.execute(
                "UPDATE jobs SET state = ?, updated_at = ?, finished_at = ?, "
                "cancel_requested_at = NULL WHERE key = ? AND generation = ?",
                (
                    state,
                    timestamp,
                    timestamp if requested else None,
                    str(row["key"]),
                    int(row["generation"]),
                ),
            )
            _event(
                conn,
                key=str(row["key"]),
                generation=int(row["generation"]),
                event_type="crash_recovered",
                detail={"state": state},
                timestamp=timestamp,
            )
            if requested:
                cancelled += 1
            else:
                queued += 1
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return {"queued": queued, "cancelled": cancelled}


@contextmanager
def lane_lock(alias: str, lane: str):
    if lane not in model.LANES:
        raise PolicyError(f"unknown jobs lane: {lane}")
    directory = path_for(alias, create_parent=True).parent
    path = directory / f"{lane}.lock"
    handle = path.open("a+")
    restrict_file(path)
    try:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise PolicyError(
                f"jobs {lane} lane is already running for account {alias!r}"
            ) from exc
        yield
    finally:
        try:
            fcntl.flock(handle, fcntl.LOCK_UN)
        finally:
            handle.close()
