"""Per-account archive SQLite/WAL store (ADR-0068)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tgcli.errors import NotFoundError, PolicyError
from tgcli.session import ensure_state_dir, restrict_file

SCHEMA_VERSION = 1
DB_NAME = "archive.db"

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS meta (
    id INTEGER PRIMARY KEY CHECK (id = 1),
    account_user_id INTEGER NOT NULL,
    account_alias TEXT NOT NULL,
    created_at TEXT NOT NULL
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
    more INTEGER NOT NULL DEFAULT 0
);
CREATE VIRTUAL TABLE IF NOT EXISTS messages_fts USING fts5(
    text,
    transcript,
    peer_id UNINDEXED,
    message_id UNINDEXED
);
"""


def connect(path: Path) -> sqlite3.Connection:
    """Open (or create) an archive DB with WAL pragmas and schema v1."""
    path.parent.mkdir(parents=True, exist_ok=True)
    created = not path.exists()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if created or version == 0:
            conn.executescript(_SCHEMA_SQL)
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            conn.commit()
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


def upsert_message(
    conn: sqlite3.Connection, peer_id: int, payload: dict[str, Any]
) -> str:
    """Insert or update a message. Returns 'inserted' | 'updated' | 'unchanged'.

    Edits that change the stored payload append a revision of the previous body.
    """
    message_id = int(payload["id"])
    text = payload.get("text")
    date = payload.get("date")
    edited_at = payload.get("edited_at")
    from_id = None
    from_block = payload.get("from")
    if isinstance(from_block, dict) and from_block.get("id") is not None:
        from_id = int(from_block["id"])
    body = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    existing = conn.execute(
        "SELECT payload, edited_at FROM messages WHERE peer_id = ? AND message_id = ?",
        (peer_id, message_id),
    ).fetchone()
    if existing is None:
        conn.execute(
            "INSERT INTO messages("
            "peer_id, message_id, date, from_id, text, edited_at, payload"
            ") VALUES (?, ?, ?, ?, ?, ?, ?)",
            (peer_id, message_id, date, from_id, text, edited_at, body),
        )
        conn.execute(
            "INSERT INTO messages_fts(text, transcript, peer_id, message_id) "
            "VALUES (?, '', ?, ?)",
            (text or "", peer_id, message_id),
        )
        return "inserted"
    if existing["payload"] == body:
        return "unchanged"
    recorded_at = datetime.now(UTC).isoformat()
    rev_key = existing["edited_at"] or recorded_at
    conn.execute(
        "INSERT OR IGNORE INTO revisions("
        "peer_id, message_id, edited_at, payload, recorded_at"
        ") VALUES (?, ?, ?, ?, ?)",
        (peer_id, message_id, rev_key, existing["payload"], recorded_at),
    )
    conn.execute(
        "UPDATE messages SET date = ?, from_id = ?, text = ?, edited_at = ?, "
        "payload = ? WHERE peer_id = ? AND message_id = ?",
        (date, from_id, text, edited_at, body, peer_id, message_id),
    )
    conn.execute(
        "DELETE FROM messages_fts WHERE peer_id = ? AND message_id = ?",
        (peer_id, message_id),
    )
    conn.execute(
        "INSERT INTO messages_fts(text, transcript, peer_id, message_id) "
        "VALUES (?, '', ?, ?)",
        (text or "", peer_id, message_id),
    )
    return "updated"


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    def _count(table: str) -> int:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    pending = conn.execute(
        "SELECT COUNT(*) FROM transcripts WHERE status = 'pending'"
    ).fetchone()[0]
    return {
        "messages": _count("messages"),
        "revisions": _count("revisions"),
        "tombstones": _count("tombstones"),
        "transcripts": _count("transcripts"),
        "scope": _count("scope"),
        "transcript_queue": int(pending),
    }


def list_scope(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT peer_id, kind, title, username, chat_ref, added_at "
        "FROM scope ORDER BY added_at, peer_id"
    ).fetchall()
    return [
        {
            "peer_id": int(row["peer_id"]),
            "kind": str(row["kind"]),
            "title": row["title"],
            "username": row["username"],
            "chat_ref": row["chat_ref"],
            "added_at": str(row["added_at"]),
        }
        for row in rows
    ]


def add_scope(
    conn: sqlite3.Connection,
    *,
    peer_id: int,
    kind: str,
    title: str | None,
    username: str | None,
    chat_ref: str,
) -> dict[str, Any]:
    now = datetime.now(UTC).isoformat()
    existing = conn.execute(
        "SELECT peer_id FROM scope WHERE peer_id = ?", (peer_id,)
    ).fetchone()
    with conn:
        conn.execute(
            "INSERT INTO scope(peer_id, kind, title, username, chat_ref, added_at) "
            "VALUES (?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(peer_id) DO UPDATE SET "
            "kind = excluded.kind, title = excluded.title, "
            "username = excluded.username, chat_ref = excluded.chat_ref",
            (peer_id, kind, title, username, chat_ref, now),
        )
    return {
        "peer_id": peer_id,
        "kind": kind,
        "title": title,
        "username": username,
        "chat_ref": chat_ref,
        "added_at": now,
        "created": existing is None,
    }


def remove_scope(conn: sqlite3.Connection, peer_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT peer_id, kind, title, username, chat_ref, added_at "
        "FROM scope WHERE peer_id = ?",
        (peer_id,),
    ).fetchone()
    if row is None:
        return None
    with conn:
        conn.execute("DELETE FROM scope WHERE peer_id = ?", (peer_id,))
    return {
        "peer_id": int(row["peer_id"]),
        "kind": str(row["kind"]),
        "title": row["title"],
        "username": row["username"],
        "chat_ref": row["chat_ref"],
        "added_at": str(row["added_at"]),
    }


def in_explicit_scope(conn: sqlite3.Connection, peer_id: int) -> bool:
    row = conn.execute("SELECT 1 FROM scope WHERE peer_id = ?", (peer_id,)).fetchone()
    return row is not None


def get_sync_state(conn: sqlite3.Connection, peer_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM sync_state WHERE peer_id = ?", (peer_id,)
    ).fetchone()
    if row is None:
        return None
    return {
        "peer_id": int(row["peer_id"]),
        "oldest_id": row["oldest_id"],
        "newest_id": row["newest_id"],
        "last_backfill_at": row["last_backfill_at"],
        "last_sync_at": row["last_sync_at"],
        "last_error": row["last_error"],
        "more": bool(row["more"]),
    }


def list_sync_state(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM sync_state ORDER BY peer_id").fetchall()
    return [
        {
            "peer_id": int(row["peer_id"]),
            "oldest_id": row["oldest_id"],
            "newest_id": row["newest_id"],
            "last_backfill_at": row["last_backfill_at"],
            "last_sync_at": row["last_sync_at"],
            "last_error": row["last_error"],
            "more": bool(row["more"]),
        }
        for row in rows
    ]


def upsert_sync_state(
    conn: sqlite3.Connection,
    peer_id: int,
    *,
    oldest_id: int | None,
    newest_id: int | None,
    more: bool,
    last_error: str | None = None,
) -> None:
    now = datetime.now(UTC).isoformat()
    existing = get_sync_state(conn, peer_id)
    if existing is None:
        conn.execute(
            "INSERT INTO sync_state("
            "peer_id, oldest_id, newest_id, last_backfill_at, last_error, more"
            ") VALUES (?, ?, ?, ?, ?, ?)",
            (peer_id, oldest_id, newest_id, now, last_error, int(more)),
        )
        return
    merged_oldest = oldest_id
    if existing["oldest_id"] is not None and oldest_id is not None:
        merged_oldest = min(int(existing["oldest_id"]), oldest_id)
    elif existing["oldest_id"] is not None:
        merged_oldest = int(existing["oldest_id"])
    merged_newest = newest_id
    if existing["newest_id"] is not None and newest_id is not None:
        merged_newest = max(int(existing["newest_id"]), newest_id)
    elif existing["newest_id"] is not None:
        merged_newest = int(existing["newest_id"])
    conn.execute(
        "UPDATE sync_state SET oldest_id = ?, newest_id = ?, "
        "last_backfill_at = ?, last_error = ?, more = ? WHERE peer_id = ?",
        (merged_oldest, merged_newest, now, last_error, int(more), peer_id),
    )


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
