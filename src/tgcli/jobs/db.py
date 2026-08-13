"""Jobs registry SQLite bootstrap, schema, and connection policy (ADR-0087)."""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

from tgcli.errors import NotFoundError, PolicyError
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


def _validate_existing(
    conn: sqlite3.Connection, alias: str, meta: tuple[int, str]
) -> None:
    version, bound_alias = meta
    if version != SCHEMA_VERSION:
        raise PolicyError(
            f"jobs registry schema {version} is unsupported; expected {SCHEMA_VERSION}"
        )
    if bound_alias != alias:
        raise PolicyError(
            f"jobs registry is bound to alias {bound_alias!r}; "
            f"selected alias is {alias!r} — refusing to merge"
        )
    required = {"meta", "jobs", "events"}
    if not required.issubset(_table_names(conn)):
        raise PolicyError("jobs registry schema is incomplete")


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
            _validate_existing(conn, alias, meta)
        _restrict_sidecars(target)
        return conn
    except Exception:
        conn.close()
        raise


def connect_existing(alias: str) -> sqlite3.Connection:
    path = path_for(alias)
    if not path.is_file():
        raise NotFoundError("jobs registry is not initialized; run: tg jobs add …")
    conn = sqlite3.connect(
        f"{path.resolve().as_uri()}?mode=ro",
        uri=True,
        timeout=BUSY_TIMEOUT_MS / 1000,
    )
    conn.row_factory = sqlite3.Row
    try:
        conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute("PRAGMA query_only = ON")
        meta = _existing_meta(conn)
        if meta is None:
            raise PolicyError("jobs registry schema is unsupported")
        _validate_existing(conn, alias, meta)
        return conn
    except Exception:
        conn.close()
        raise


def connect_mutating(alias: str) -> sqlite3.Connection:
    """Open an existing registry and restore its mutation-time permissions."""
    path = path_for(alias)
    if not path.is_file():
        raise NotFoundError("jobs registry is not initialized; run: tg jobs add …")
    path_for(alias, create_parent=True)
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


def bind_user(conn: sqlite3.Connection, user_id: int) -> dict[str, Any]:
    """Bind the registry once, then refuse a different live Telegram user."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT account_user_id FROM meta WHERE singleton = 1"
        ).fetchone()
        if row is None:
            raise PolicyError("jobs registry metadata is missing")
        existing = row["account_user_id"]
        if existing is None:
            conn.execute(
                "UPDATE meta SET account_user_id = ? WHERE singleton = 1",
                (user_id,),
            )
        elif int(existing) != user_id:
            raise PolicyError(
                f"jobs registry is bound to Telegram user {int(existing)}; "
                f"live session is user {user_id} — refusing to merge"
            )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return read_meta(conn)


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
