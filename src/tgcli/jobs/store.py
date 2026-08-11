"""Account-scoped SQLite job registry and lane locks (ADR-0087)."""

from __future__ import annotations

import fcntl
import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from tgcli.errors import NotFoundError, PolicyError
from tgcli.jobs import model
from tgcli.session import ensure_state_dir, restrict_file, state_dir

SCHEMA_VERSION = 1
BUSY_TIMEOUT_MS = 5_000

_SCHEMA = """
CREATE TABLE meta (
    singleton       INTEGER PRIMARY KEY CHECK (singleton = 1),
    schema_version  INTEGER NOT NULL,
    account_alias   TEXT NOT NULL,
    account_user_id INTEGER
);
CREATE TABLE jobs (
    key                 TEXT NOT NULL,
    generation          INTEGER NOT NULL,
    kind                TEXT NOT NULL,
    lane                TEXT NOT NULL,
    spec_json           TEXT NOT NULL,
    spec_hash           TEXT NOT NULL,
    priority            INTEGER NOT NULL,
    state               TEXT NOT NULL,
    created_at          TEXT NOT NULL,
    updated_at          TEXT NOT NULL,
    started_at          TEXT,
    finished_at         TEXT,
    not_before          TEXT,
    last_run_at         TEXT,
    failure_streak      INTEGER NOT NULL DEFAULT 0,
    skipped_quanta      INTEGER NOT NULL DEFAULT 0,
    cancel_requested_at TEXT,
    last_result_json    TEXT,
    last_error_json     TEXT,
    quantum_count       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (key, generation),
    CHECK (lane IN ('telegram', 'local')),
    CHECK (state IN ('queued', 'running', 'completed', 'failed', 'cancelled')),
    CHECK (priority BETWEEN 0 AND 2)
);
CREATE UNIQUE INDEX jobs_one_active_generation
ON jobs(key) WHERE state IN ('queued', 'running');
CREATE INDEX jobs_lane_eligibility
ON jobs(lane, state, not_before, priority, last_run_at, key);
CREATE TABLE events (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    key         TEXT NOT NULL,
    generation  INTEGER NOT NULL,
    timestamp   TEXT NOT NULL,
    type        TEXT NOT NULL,
    detail_json TEXT NOT NULL,
    FOREIGN KEY (key, generation) REFERENCES jobs(key, generation)
);
CREATE INDEX events_key_order ON events(key, id);
"""


def _stamp(now: datetime | None = None) -> str:
    return (now or datetime.now(UTC)).isoformat(timespec="seconds")


def path_for(alias: str, *, create_parent: bool = False) -> Path:
    if (
        not alias
        or alias in (".", "..")
        or Path(alias).name != alias
        or "/" in alias
        or "\\" in alias
        or any(ord(char) < 32 for char in alias)
    ):
        raise PolicyError(f"invalid account alias for jobs registry: {alias!r}")
    if create_parent:
        directory = ensure_state_dir("jobs", alias)
    else:
        directory = state_dir() / "jobs" / alias
    return directory / "jobs.db"


def _restrict_sidecars(path: Path) -> None:
    restrict_file(path)
    for suffix in ("-wal", "-shm"):
        restrict_file(Path(f"{path}{suffix}"))


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        ).fetchall()
        if not str(row[0]).startswith("sqlite_")
    }


def _existing_meta(conn: sqlite3.Connection) -> tuple[int, str] | None:
    if "meta" not in _table_names(conn):
        return None
    try:
        row = conn.execute(
            "SELECT schema_version, account_alias FROM meta WHERE singleton = 1"
        ).fetchone()
    except sqlite3.Error as exc:
        try:
            version_row = conn.execute(
                "SELECT schema_version FROM meta LIMIT 1"
            ).fetchone()
        except sqlite3.Error:
            version_row = None
        version = None if version_row is None else version_row[0]
        raise PolicyError(f"jobs registry schema is unsupported: {version!r}") from exc
    if row is None:
        raise PolicyError("jobs registry metadata is missing")
    return int(row["schema_version"]), str(row["account_alias"])


def connect(alias: str, *, path: Path | None = None) -> sqlite3.Connection:
    target = path or path_for(alias, create_parent=True)
    if path is not None:
        target.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
    existed = target.exists()
    conn = sqlite3.connect(target, timeout=BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA journal_mode = WAL")
        meta = _existing_meta(conn)
        if meta is None:
            if existed and _table_names(conn):
                raise PolicyError("jobs registry schema is unsupported")
            conn.executescript(_SCHEMA)
            conn.execute(
                "INSERT INTO meta(singleton, schema_version, account_alias) "
                "VALUES (1, ?, ?)",
                (SCHEMA_VERSION, alias),
            )
            conn.execute(f"PRAGMA user_version = {SCHEMA_VERSION}")
            conn.commit()
        else:
            version, bound_alias = meta
            if version != SCHEMA_VERSION:
                raise PolicyError(
                    f"jobs registry schema {version} is unsupported; "
                    f"expected {SCHEMA_VERSION}"
                )
            if bound_alias != alias:
                raise PolicyError(
                    f"jobs registry is bound to alias {bound_alias!r}; "
                    f"selected alias is {alias!r} — refusing to merge"
                )
            required = {"meta", "jobs", "events"}
            if not required.issubset(_table_names(conn)):
                raise PolicyError("jobs registry schema is incomplete")
        _restrict_sidecars(target)
        return conn
    except Exception:
        conn.close()
        raise


def connect_existing(alias: str) -> sqlite3.Connection:
    path = path_for(alias)
    if not path.is_file():
        raise NotFoundError("jobs registry is not initialized; run: tg jobs add …")
    return connect(alias, path=path)


def read_meta(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM meta WHERE singleton = 1").fetchone()
    if row is None:
        raise PolicyError("jobs registry metadata is missing")
    return {
        "schema_version": int(row["schema_version"]),
        "account_alias": str(row["account_alias"]),
        "account_user_id": (
            None if row["account_user_id"] is None else int(row["account_user_id"])
        ),
    }


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
    if streak >= 3:
        retry_at = moment + timedelta(hours=2)
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
    delay = timedelta(minutes=5 if streak == 1 else 30)
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


def inventory(root: Path) -> dict[str, Any]:
    jobs_root = root / "jobs"
    db_files = sorted(jobs_root.glob("*/jobs.db")) if jobs_root.is_dir() else []
    wal_files = sorted(jobs_root.glob("*/jobs.db-wal")) if jobs_root.is_dir() else []
    shm_files = sorted(jobs_root.glob("*/jobs.db-shm")) if jobs_root.is_dir() else []
    states: dict[str, int] = {}
    unreadable = 0
    for path in db_files:
        try:
            conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True)
            rows = conn.execute(
                "SELECT state, COUNT(*) FROM jobs j WHERE generation = "
                "(SELECT MAX(generation) FROM jobs WHERE key = j.key) GROUP BY state"
            ).fetchall()
            conn.close()
        except sqlite3.Error:
            unreadable += 1
            continue
        for state, count in rows:
            states[str(state)] = states.get(str(state), 0) + int(count)

    def total(paths: list[Path]) -> int:
        size = 0
        for path in paths:
            try:
                size += path.stat().st_size
            except OSError:
                pass
        return size

    return {
        "db": {"count": len(db_files), "bytes": total(db_files)},
        "wal": {"count": len(wal_files), "bytes": total(wal_files)},
        "shm": {"count": len(shm_files), "bytes": total(shm_files)},
        "states": dict(sorted(states.items())),
        "unreadable": unreadable,
    }
