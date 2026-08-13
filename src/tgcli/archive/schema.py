"""Archive SQLite schema, connect, migrations, and meta (ADR-0068/0116)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tgcli.errors import NotFoundError, PolicyError
from tgcli.session import ensure_state_dir, restrict_file

SCHEMA_VERSION = 8
DB_NAME = "archive.db"
TRANSCRIBABLE_MEDIA_KINDS = ("voice", "video_note")
NO_TRANSCRIPT_MARKER = "no_transcript no transcript"
# Explicitly pin archive contention behavior alongside jobs/governor
# (ADR-0096); do not inherit the driver's timeout default.
BUSY_TIMEOUT_MS = 5_000

_FTS_TOKENIZER = 'tokenize = "unicode61 remove_diacritics 2"'


def _casefold(value: str | None) -> str | None:
    return value.casefold() if value is not None else None


_SCHEMA_SQL = f"""
CREATE TABLE IF NOT EXISTS meta (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    account_user_id INTEGER NOT NULL,
    account_alias TEXT NOT NULL,
    created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS account_sync (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    changes_cursor TEXT,
    gap_json TEXT,
    last_sync_at TEXT,
    last_reconcile_at TEXT,
    reconcile_json TEXT,
    private_enum_json TEXT
);
CREATE TABLE IF NOT EXISTS messages (
    peer_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    date TEXT,
    from_id INTEGER,
    text TEXT,
    edited_at TEXT,
    payload TEXT NOT NULL,
    PRIMARY KEY (peer_id, message_id)
);
CREATE TABLE IF NOT EXISTS revisions (
    peer_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    edited_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    recorded_at TEXT NOT NULL,
    PRIMARY KEY (peer_id, message_id, edited_at)
);
CREATE TABLE IF NOT EXISTS tombstones (
    peer_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    deleted_at TEXT NOT NULL,
    PRIMARY KEY (peer_id, message_id)
);
CREATE TABLE IF NOT EXISTS transcripts (
    peer_id INTEGER NOT NULL,
    message_id INTEGER NOT NULL,
    text TEXT,
    model TEXT,
    model_version TEXT,
    status TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    updated_at TEXT NOT NULL,
    media_path TEXT,
    media_kind TEXT,
    media_attempts INTEGER NOT NULL DEFAULT 0,
    media_status TEXT NOT NULL DEFAULT 'pending',
    last_error TEXT,
    PRIMARY KEY (peer_id, message_id)
);
CREATE TABLE IF NOT EXISTS scope (
    peer_id INTEGER PRIMARY KEY,
    kind TEXT NOT NULL,
    title TEXT,
    username TEXT,
    chat_ref TEXT,
    added_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS sync_state (
    peer_id INTEGER PRIMARY KEY,
    oldest_id INTEGER,
    newest_id INTEGER,
    last_backfill_at TEXT,
    last_sync_at TEXT,
    last_error TEXT,
    more INTEGER NOT NULL DEFAULT 0,
    kind TEXT,
    title TEXT,
    username TEXT,
    chat_ref TEXT
);
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    text,
    transcript,
    peer_id UNINDEXED,
    message_id UNINDEXED,
    {_FTS_TOKENIZER}
);
"""


def fold_yo(text: str) -> str:
    """Map Cyrillic yo→ye for FTS indexing/queries.

    SQLite's ``unicode61 remove_diacritics 2`` folds Latin diacritics but does
    not treat Cyrillic ``ё`` as ``е`` + diaeresis on current libsqlite builds,
    so archive search applies this thin fold at FTS write and MATCH time.
    ``messages.text`` stays unmodified.
    """
    return text.replace("ё", "е").replace("Ё", "Е")


def connect(path: Path) -> sqlite3.Connection:
    """Open (or create) an archive DB with WAL pragmas and current schema.

    Concurrent sync + search/transcribe on the same file is supported at the
    SQLite level via WAL + ``busy_timeout``; writers still serialize. Prefer
    one sync writer at a time for predictable pacing.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    created = not path.exists()
    conn = sqlite3.connect(path, timeout=BUSY_TIMEOUT_MS / 1000)
    conn.row_factory = sqlite3.Row
    conn.create_function("tgcli_casefold", 1, _casefold)
    try:
        conn.execute(f"PRAGMA busy_timeout = {BUSY_TIMEOUT_MS}")
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if created or version == 0:
            conn.executescript(_SCHEMA_SQL)
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            conn.commit()
        elif 1 <= version < SCHEMA_VERSION:
            if version <= 1:
                _migrate_v1_to_v2(conn)
            if version <= 2:
                _migrate_v2_to_v3(conn)
            if version <= 3:
                _migrate_v3_to_v4(conn)
            if version <= 5:
                _migrate_v5_to_v6(conn)
            if version <= 6:
                _migrate_v6_to_v7(conn)
            if version <= 7:
                _migrate_v7_to_v8(conn)
        elif version != SCHEMA_VERSION:
            conn.close()
            raise PolicyError(
                f"archive store {path.name} has unsupported schema version "
                f"{version}; expected {SCHEMA_VERSION}"
            )
        _require_integrity(conn, path)
    except PolicyError:
        raise
    except sqlite3.Error as exc:
        conn.close()
        raise PolicyError(
            f"archive store {path.name} is corrupted; manual repair is required"
        ) from exc
    if created:
        restrict_file(path)
    _restrict_sidecars(path)
    return conn


def _migrate_v1_to_v2(conn: sqlite3.Connection) -> None:
    """Rebuild messages_fts with unicode61 remove_diacritics 2; keep tables."""
    with conn:
        conn.execute("DROP TABLE IF EXISTS messages_fts")
        conn.execute(
            f"""
            CREATE VIRTUAL TABLE messages_fts USING fts5(
                text,
                transcript,
                peer_id UNINDEXED,
                message_id UNINDEXED,
                {_FTS_TOKENIZER}
            )
            """
        )
        rows = conn.execute(
            """
            SELECT m.peer_id, m.message_id, m.text, t.text AS transcript
            FROM messages AS m
            LEFT JOIN transcripts AS t
              ON t.peer_id = m.peer_id AND t.message_id = m.message_id
            """
        ).fetchall()
        conn.executemany(
            "INSERT INTO messages_fts(text, transcript, peer_id, message_id) "
            "VALUES (?, ?, ?, ?)",
            [
                (
                    fold_yo(row["text"] or ""),
                    fold_yo(row["transcript"] or ""),
                    int(row["peer_id"]),
                    int(row["message_id"]),
                )
                for row in rows
            ],
        )
        conn.execute("PRAGMA user_version=2")


def _migrate_v2_to_v3(conn: sqlite3.Connection) -> None:
    """Add account_sync + sync_state identity columns (Phase 3)."""
    with conn:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS account_sync (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                changes_cursor TEXT,
                gap_json TEXT,
                last_sync_at TEXT,
                last_reconcile_at TEXT,
                reconcile_json TEXT
            )
            """
        )
        cols = {
            row[1] for row in conn.execute("PRAGMA table_info(sync_state)").fetchall()
        }
        for name in ("kind", "title", "username", "chat_ref"):
            if name not in cols:
                conn.execute(f"ALTER TABLE sync_state ADD COLUMN {name} TEXT")
        conn.execute("PRAGMA user_version=3")


def _migrate_v3_to_v4(conn: sqlite3.Connection) -> None:
    """Add media acquisition metadata to the transcript queue."""
    with conn:
        cols = {
            row[1] for row in conn.execute("PRAGMA table_info(transcripts)").fetchall()
        }
        for name, declaration in (
            ("media_path", "TEXT"),
            ("media_kind", "TEXT"),
            ("last_error", "TEXT"),
        ):
            if name not in cols:
                conn.execute(f"ALTER TABLE transcripts ADD COLUMN {name} {declaration}")
        conn.execute("PRAGMA user_version=4")


def _migrate_v5_to_v6(conn: sqlite3.Connection) -> None:
    """Add independent media retry state without changing transcript attempts."""
    with conn:
        cols = {
            row[1] for row in conn.execute("PRAGMA table_info(transcripts)").fetchall()
        }
        for name, declaration in (
            ("media_attempts", "INTEGER NOT NULL DEFAULT 0"),
            ("media_status", "TEXT NOT NULL DEFAULT 'pending'"),
        ):
            if name not in cols:
                conn.execute(f"ALTER TABLE transcripts ADD COLUMN {name} {declaration}")
        conn.execute(
            "UPDATE transcripts SET media_status = 'done' "
            "WHERE media_path IS NOT NULL AND media_path <> ''"
        )
        conn.execute("PRAGMA user_version=6")


def _migrate_v6_to_v7(conn: sqlite3.Connection) -> None:
    """Remove the superseded archive-refresh failure state."""
    with conn:
        conn.execute("ALTER TABLE account_sync RENAME TO account_sync_v6")
        conn.execute(
            """
            CREATE TABLE account_sync (
                id INTEGER PRIMARY KEY CHECK (id = 1),
                changes_cursor TEXT,
                gap_json TEXT,
                last_sync_at TEXT,
                last_reconcile_at TEXT,
                reconcile_json TEXT
            )
            """
        )
        conn.execute(
            "INSERT INTO account_sync("
            "id, changes_cursor, gap_json, last_sync_at, last_reconcile_at, "
            "reconcile_json"
            ") SELECT id, changes_cursor, gap_json, last_sync_at, "
            "last_reconcile_at, reconcile_json FROM account_sync_v6"
        )
        conn.execute("DROP TABLE account_sync_v6")
        conn.execute("PRAGMA user_version=7")


def _migrate_v7_to_v8(conn: sqlite3.Connection) -> None:
    """Persist private-dialog enumeration resume token (ADR-0118)."""
    with conn:
        cols = {
            row[1] for row in conn.execute("PRAGMA table_info(account_sync)").fetchall()
        }
        if "private_enum_json" not in cols:
            conn.execute("ALTER TABLE account_sync ADD COLUMN private_enum_json TEXT")
        conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")


def integrity_report(conn: sqlite3.Connection) -> str:
    rows = conn.execute("PRAGMA integrity_check").fetchall()
    if len(rows) == 1 and rows[0][0] == "ok":
        return "ok"
    return "; ".join(str(row[0]) for row in rows)


def schema_version(conn: sqlite3.Connection) -> int:
    return int(conn.execute("PRAGMA user_version").fetchone()[0])


def _require_integrity(conn: sqlite3.Connection, path: Path) -> None:
    report = integrity_report(conn)
    if report != "ok":
        conn.close()
        raise PolicyError(
            f"archive store {path.name} failed integrity check ({report}); "
            "manual repair is required"
        )


def _restrict_sidecars(path: Path) -> None:
    for suffix in ("-wal", "-shm"):
        sidecar = Path(str(path) + suffix)
        if sidecar.exists():
            restrict_file(sidecar)


def ensure_meta(
    conn: sqlite3.Connection,
    *,
    account_user_id: int,
    account_alias: str,
) -> bool:
    """Bind or verify store identity; True when a new meta row was written."""
    row = conn.execute("SELECT * FROM meta WHERE id = 1").fetchone()
    now = datetime.now(UTC).isoformat()
    if row is None:
        with conn:
            conn.execute(
                "INSERT INTO meta(id, account_user_id, account_alias, created_at) "
                "VALUES (1, ?, ?, ?)",
                (account_user_id, account_alias, now),
            )
        return True
    if int(row["account_user_id"]) != account_user_id:
        raise PolicyError(
            f"archive store is bound to account user_id {row['account_user_id']}; "
            f"live session is {account_user_id} — refusing to merge"
        )
    if str(row["account_alias"]) != account_alias:
        raise PolicyError(
            f"archive store is bound to alias {row['account_alias']!r}; "
            f"selected alias is {account_alias!r} — refusing to merge"
        )
    return False


def read_meta(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM meta WHERE id = 1").fetchone()
    if row is None:
        raise NotFoundError("archive store is not initialized; run: tg archive init")
    return {
        "account_user_id": int(row["account_user_id"]),
        "account_alias": str(row["account_alias"]),
        "created_at": str(row["created_at"]),
    }


def require_bound_alias(conn: sqlite3.Connection, alias: str) -> dict[str, Any]:
    meta = read_meta(conn)
    if meta["account_alias"] != alias:
        raise PolicyError(
            f"archive store is bound to alias {meta['account_alias']!r}; "
            f"selected alias is {alias!r} — refusing to merge"
        )
    return meta


def require_bound_user(
    conn: sqlite3.Connection, user_id: int, alias: str
) -> dict[str, Any]:
    meta = require_bound_alias(conn, alias)
    if meta["account_user_id"] != user_id:
        raise PolicyError(
            f"archive store is bound to account user_id {meta['account_user_id']}; "
            f"live session is {user_id} — refusing to merge"
        )
    return meta


def db_path_for(account_dir: Path) -> Path:
    return account_dir / DB_NAME


def ensure_account_dir(root: Path, alias: str) -> Path:
    """Create ``root/alias`` at 0700 (and repair modes under the archive root)."""
    # root itself may be a custom path outside state_dir; still force 0700.
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    try:
        if root.stat().st_mode & 0o777 != 0o700:
            root.chmod(0o700)
    except OSError:
        pass
    path = root / alias
    path.mkdir(mode=0o700, exist_ok=True)
    try:
        if path.stat().st_mode & 0o777 != 0o700:
            path.chmod(0o700)
    except OSError:
        pass
    return path


def default_archive_root() -> Path:
    return ensure_state_dir("archive")
