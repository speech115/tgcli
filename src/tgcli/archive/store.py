"""Per-account archive SQLite/WAL store (ADR-0068)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tgcli.errors import NotFoundError, PolicyError
from tgcli.session import ensure_state_dir, restrict_file

SCHEMA_VERSION = 4
DB_NAME = "archive.db"
MEDIA_DIR_NAME = "media"
TRANSCRIBABLE_MEDIA_KINDS = ("voice", "video_note")
NO_TRANSCRIPT_MARKER = "no_transcript no transcript"

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
    reconcile_json TEXT
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
    """Open (or create) an archive DB with WAL pragmas and current schema."""
    path.parent.mkdir(parents=True, exist_ok=True)
    created = not path.exists()
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.create_function("tgcli_casefold", 1, _casefold)
    try:
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        conn.execute("PRAGMA foreign_keys=ON")
        version = conn.execute("PRAGMA user_version").fetchone()[0]
        if created or version == 0:
            conn.executescript(_SCHEMA_SQL)
            conn.execute(f"PRAGMA user_version={SCHEMA_VERSION}")
            conn.commit()
        elif version == 1:
            _migrate_v1_to_v2(conn)
            _migrate_v2_to_v3(conn)
            _migrate_v3_to_v4(conn)
        elif version == 2:
            _migrate_v2_to_v3(conn)
            _migrate_v3_to_v4(conn)
        elif version == 3:
            _migrate_v3_to_v4(conn)
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


def upsert_message(
    conn: sqlite3.Connection,
    peer_id: int,
    payload: dict[str, Any],
    *,
    media_kind: str | None = None,
) -> str:
    """Insert or update a message. Returns 'inserted' | 'updated' | 'unchanged'.

    Edits that change the stored payload append a revision of the previous body.
    """
    message_id = int(payload["id"])
    media_kind = media_kind or payload.get("media_kind")
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
            (fold_yo(text or ""), peer_id, message_id),
        )
        if media_kind in TRANSCRIBABLE_MEDIA_KINDS:
            ensure_transcript_queue(conn, peer_id, message_id, media_kind=media_kind)
        return "inserted"
    if existing["payload"] == body:
        if media_kind in TRANSCRIBABLE_MEDIA_KINDS:
            ensure_transcript_queue(conn, peer_id, message_id, media_kind=media_kind)
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
    _replace_fts_row(conn, peer_id, message_id, text)
    if media_kind in TRANSCRIBABLE_MEDIA_KINDS:
        ensure_transcript_queue(conn, peer_id, message_id, media_kind=media_kind)
    return "updated"


def _transcript_text(conn: sqlite3.Connection, peer_id: int, message_id: int) -> str:
    row = conn.execute(
        "SELECT text, status FROM transcripts WHERE peer_id = ? AND message_id = ?",
        (peer_id, message_id),
    ).fetchone()
    if row is None:
        return ""
    if row["text"]:
        return fold_yo(row["text"])
    if row["status"] == "no_transcript":
        return NO_TRANSCRIPT_MARKER
    return ""


def _replace_fts_row(
    conn: sqlite3.Connection, peer_id: int, message_id: int, text: str | None
) -> None:
    """Replace one FTS row without dropping its stored transcript."""
    conn.execute(
        "DELETE FROM messages_fts WHERE peer_id = ? AND message_id = ?",
        (peer_id, message_id),
    )
    conn.execute(
        "INSERT INTO messages_fts(text, transcript, peer_id, message_id) "
        "VALUES (?, ?, ?, ?)",
        (
            fold_yo(text or ""),
            _transcript_text(conn, peer_id, message_id),
            peer_id,
            message_id,
        ),
    )


def ensure_transcript_queue(
    conn: sqlite3.Connection,
    peer_id: int,
    message_id: int,
    *,
    media_kind: str,
) -> None:
    """Create or enrich the queue row for one voice/video-note message."""
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO transcripts("
        "peer_id, message_id, text, model, model_version, status, attempts, "
        "updated_at, media_path, media_kind, last_error"
        ") VALUES (?, ?, NULL, NULL, NULL, 'pending', 0, ?, NULL, ?, NULL) "
        "ON CONFLICT(peer_id, message_id) DO UPDATE SET "
        "media_kind = excluded.media_kind, updated_at = excluded.updated_at",
        (peer_id, message_id, now, media_kind),
    )


def transcript_row(
    conn: sqlite3.Connection, peer_id: int, message_id: int
) -> dict[str, Any] | None:
    row = conn.execute(
        "SELECT * FROM transcripts WHERE peer_id = ? AND message_id = ?",
        (peer_id, message_id),
    ).fetchone()
    return _transcript_row(row) if row is not None else None


def _transcript_row(row: sqlite3.Row) -> dict[str, Any]:
    return {
        "peer_id": int(row["peer_id"]),
        "message_id": int(row["message_id"]),
        "text": row["text"],
        "model": row["model"],
        "model_version": row["model_version"],
        "status": str(row["status"]),
        "attempts": int(row["attempts"]),
        "updated_at": str(row["updated_at"]),
        "media_path": row["media_path"],
        "media_kind": row["media_kind"],
        "last_error": row["last_error"],
    }


def list_transcript_queue(
    conn: sqlite3.Connection, *, limit: int, max_attempts: int
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT t.*, m.date "
        "FROM transcripts AS t JOIN messages AS m "
        "ON m.peer_id = t.peer_id AND m.message_id = t.message_id "
        "WHERE t.status IN ('pending', 'retryable') "
        "AND t.media_path IS NOT NULL AND t.attempts < ? "
        "ORDER BY m.date DESC, t.peer_id DESC, t.message_id DESC LIMIT ?",
        (max_attempts, limit),
    ).fetchall()
    return [_transcript_row(row) | {"date": row["date"]} for row in rows]


def set_media_path(
    conn: sqlite3.Connection,
    peer_id: int,
    message_id: int,
    *,
    path: str,
    media_kind: str,
) -> None:
    """Record a downloaded media path while keeping the queue retryable."""
    ensure_transcript_queue(conn, peer_id, message_id, media_kind=media_kind)
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "UPDATE transcripts SET media_path = ?, media_kind = ?, "
        "status = CASE WHEN status = 'no_transcript' THEN 'pending' ELSE status END, "
        "last_error = NULL, updated_at = ? "
        "WHERE peer_id = ? AND message_id = ?",
        (path, media_kind, now, peer_id, message_id),
    )
    message = conn.execute(
        "SELECT text FROM messages WHERE peer_id = ? AND message_id = ?",
        (peer_id, message_id),
    ).fetchone()
    _replace_fts_row(
        conn,
        peer_id,
        message_id,
        message["text"] if message is not None else "",
    )


def record_media_failure(
    conn: sqlite3.Connection, peer_id: int, message_id: int, *, error: str
) -> None:
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "UPDATE transcripts SET media_path = NULL, last_error = ?, updated_at = ? "
        "WHERE peer_id = ? AND message_id = ?",
        (error, now, peer_id, message_id),
    )


def record_transcript_success(
    conn: sqlite3.Connection,
    peer_id: int,
    message_id: int,
    *,
    text: str,
    model: str,
    model_version: str,
) -> None:
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "UPDATE transcripts SET text = ?, model = ?, model_version = ?, "
        "status = 'done', last_error = NULL, updated_at = ? "
        "WHERE peer_id = ? AND message_id = ?",
        (text, model, model_version, now, peer_id, message_id),
    )
    message = conn.execute(
        "SELECT text FROM messages WHERE peer_id = ? AND message_id = ?",
        (peer_id, message_id),
    ).fetchone()
    _replace_fts_row(
        conn,
        peer_id,
        message_id,
        message["text"] if message is not None else "",
    )


def record_transcript_failure(
    conn: sqlite3.Connection,
    peer_id: int,
    message_id: int,
    *,
    status: str,
    error: str,
) -> None:
    if status not in {"retryable", "no_transcript"}:
        raise PolicyError(f"unsupported transcript failure status: {status}")
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "UPDATE transcripts SET attempts = attempts + 1, status = ?, "
        "last_error = ?, updated_at = ? WHERE peer_id = ? AND message_id = ?",
        (status, error, now, peer_id, message_id),
    )
    message = conn.execute(
        "SELECT text FROM messages WHERE peer_id = ? AND message_id = ?",
        (peer_id, message_id),
    ).fetchone()
    _replace_fts_row(
        conn,
        peer_id,
        message_id,
        message["text"] if message is not None else "",
    )


def media_relative_path(
    peer_id: int, message_id: int, *, media_kind: str, mime: str | None = None
) -> str:
    suffix = ".bin"
    if media_kind == "voice":
        suffix = ".ogg"
    elif media_kind == "video_note":
        suffix = ".mp4"
    elif mime:
        suffix = {
            "audio/mpeg": ".mp3",
            "audio/mp4": ".m4a",
            "video/mp4": ".mp4",
        }.get(mime, suffix)
    return f"{MEDIA_DIR_NAME}/{peer_id}/{message_id}{suffix}"


def counts(conn: sqlite3.Connection) -> dict[str, int]:
    def _count(table: str) -> int:
        return int(conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

    pending = conn.execute(
        "SELECT COUNT(*) FROM transcripts WHERE status IN ('pending', 'retryable')"
    ).fetchone()[0]
    return {
        "messages": _count("messages"),
        "revisions": _count("revisions"),
        "tombstones": _count("tombstones"),
        "transcripts": _count("transcripts"),
        "scope": _count("scope"),
        "transcript_queue": int(pending),
    }


def transcript_status_counts(conn: sqlite3.Connection) -> dict[str, int]:
    counts: dict[str, int] = {}
    for status in ("pending", "retryable", "done", "no_transcript"):
        counts[status] = 0
    rows = conn.execute(
        "SELECT status, COUNT(*) AS count FROM transcripts GROUP BY status"
    ).fetchall()
    for row in rows:
        counts[str(row["status"])] = int(row["count"])
    return counts


def transcript_errors(
    conn: sqlite3.Connection, *, limit: int = 20
) -> list[dict[str, Any]]:
    rows = conn.execute(
        "SELECT peer_id, message_id, status, attempts, updated_at, last_error "
        "FROM transcripts WHERE last_error IS NOT NULL "
        "ORDER BY updated_at DESC, peer_id DESC, message_id DESC LIMIT ?",
        (limit,),
    ).fetchall()
    return [
        {
            "peer_id": int(row["peer_id"]),
            "message_id": int(row["message_id"]),
            "status": str(row["status"]),
            "attempts": int(row["attempts"]),
            "updated_at": str(row["updated_at"]),
            "error": str(row["last_error"]),
        }
        for row in rows
    ]


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
    return _sync_state_row(row)


def list_sync_state(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute("SELECT * FROM sync_state ORDER BY peer_id").fetchall()
    return [_sync_state_row(row) for row in rows]


def _sync_state_row(row: sqlite3.Row) -> dict[str, Any]:
    keys = set(row.keys())
    return {
        "peer_id": int(row["peer_id"]),
        "oldest_id": row["oldest_id"],
        "newest_id": row["newest_id"],
        "last_backfill_at": row["last_backfill_at"],
        "last_sync_at": row["last_sync_at"],
        "last_error": row["last_error"],
        "more": bool(row["more"]),
        "kind": row["kind"] if "kind" in keys else None,
        "title": row["title"] if "title" in keys else None,
        "username": row["username"] if "username" in keys else None,
        "chat_ref": row["chat_ref"] if "chat_ref" in keys else None,
    }


def upsert_sync_state(
    conn: sqlite3.Connection,
    peer_id: int,
    *,
    oldest_id: int | None,
    newest_id: int | None,
    more: bool,
    last_error: str | None = None,
    kind: str | None = None,
    title: str | None = None,
    username: str | None = None,
    chat_ref: str | None = None,
    touch_sync: bool = False,
) -> None:
    now = datetime.now(UTC).isoformat()
    existing = get_sync_state(conn, peer_id)
    if existing is None:
        conn.execute(
            "INSERT INTO sync_state("
            "peer_id, oldest_id, newest_id, last_backfill_at, last_sync_at, "
            "last_error, more, kind, title, username, chat_ref"
            ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                peer_id,
                oldest_id,
                newest_id,
                None if touch_sync else now,
                now if touch_sync else None,
                last_error,
                int(more),
                kind,
                title,
                username,
                chat_ref,
            ),
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
    merged_kind = kind if kind is not None else existing.get("kind")
    merged_title = title if title is not None else existing.get("title")
    merged_username = username if username is not None else existing.get("username")
    merged_ref = chat_ref if chat_ref is not None else existing.get("chat_ref")
    last_backfill = existing["last_backfill_at"] if touch_sync else now
    last_sync = now if touch_sync else existing["last_sync_at"]
    conn.execute(
        "UPDATE sync_state SET oldest_id = ?, newest_id = ?, "
        "last_backfill_at = ?, last_sync_at = ?, last_error = ?, more = ?, "
        "kind = ?, title = ?, username = ?, chat_ref = ? WHERE peer_id = ?",
        (
            merged_oldest,
            merged_newest,
            last_backfill,
            last_sync,
            last_error,
            int(more),
            merged_kind,
            merged_title,
            merged_username,
            merged_ref,
            peer_id,
        ),
    )


def insert_tombstone(
    conn: sqlite3.Connection,
    peer_id: int,
    message_id: int,
    *,
    deleted_at: str | None = None,
) -> bool:
    """Insert a deletion tombstone. Returns True when a new row was written."""
    when = deleted_at or datetime.now(UTC).isoformat()
    before = conn.execute(
        "SELECT 1 FROM tombstones WHERE peer_id = ? AND message_id = ?",
        (peer_id, message_id),
    ).fetchone()
    conn.execute(
        "INSERT OR IGNORE INTO tombstones(peer_id, message_id, deleted_at) "
        "VALUES (?, ?, ?)",
        (peer_id, message_id, when),
    )
    return before is None


def find_message_peers(
    conn: sqlite3.Connection,
    message_ids: list[int],
    *,
    exclude_channels: bool = True,
) -> list[tuple[int, int]]:
    """Return ``(peer_id, message_id)`` rows present for the given ids.

    Peer-less private deletes only come from common difference (users /
    basic groups). Channel/supergroup ids use the ``-100…`` marked space
    and must not be tombstoned by id collision alone.
    """
    if not message_ids:
        return []
    placeholders = ",".join("?" for _ in message_ids)
    channel_clause = ""
    params: list[int] = [int(mid) for mid in message_ids]
    if exclude_channels:
        # Marked channel/supergroup ids are <= -10**12 (-100XXXXXXXXXX).
        channel_clause = " AND peer_id > ?"
        params.append(-(10**12))
    rows = conn.execute(
        f"SELECT peer_id, message_id FROM messages "
        f"WHERE message_id IN ({placeholders}){channel_clause}",
        params,
    ).fetchall()
    return [(int(row["peer_id"]), int(row["message_id"])) for row in rows]


def list_sync_identity(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Identity rows used by offline ``--chat`` resolution (private peers)."""
    return [
        row
        for row in list_sync_state(conn)
        if row.get("chat_ref") or row.get("username") or row.get("title")
    ]


def read_account_sync(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM account_sync WHERE id = 1").fetchone()
    if row is None:
        return {
            "changes_cursor": None,
            "gap": None,
            "last_sync_at": None,
            "last_reconcile_at": None,
            "reconcile": None,
        }
    gap = None
    if row["gap_json"]:
        gap = json.loads(row["gap_json"])
    reconcile = None
    if row["reconcile_json"]:
        reconcile = json.loads(row["reconcile_json"])
    return {
        "changes_cursor": row["changes_cursor"],
        "gap": gap,
        "last_sync_at": row["last_sync_at"],
        "last_reconcile_at": row["last_reconcile_at"],
        "reconcile": reconcile,
    }


def write_account_sync(
    conn: sqlite3.Connection,
    *,
    changes_cursor: str | None = None,
    gap: dict[str, Any] | None = None,
    touch_sync: bool = False,
    reconcile: dict[str, Any] | None = None,
    clear_gap: bool = False,
) -> None:
    existing = read_account_sync(conn)
    now = datetime.now(UTC).isoformat()
    cursor = (
        changes_cursor if changes_cursor is not None else existing["changes_cursor"]
    )
    if clear_gap:
        gap_json = None
    elif gap is not None:
        gap_json = json.dumps(gap, ensure_ascii=False, separators=(",", ":"))
    elif existing["gap"] is not None:
        gap_json = json.dumps(
            existing["gap"], ensure_ascii=False, separators=(",", ":")
        )
    else:
        gap_json = None
    last_sync = now if touch_sync else existing["last_sync_at"]
    if reconcile is not None:
        reconcile_json = json.dumps(
            reconcile, ensure_ascii=False, separators=(",", ":")
        )
        last_reconcile = now
    else:
        reconcile_json = (
            json.dumps(existing["reconcile"], ensure_ascii=False, separators=(",", ":"))
            if existing["reconcile"] is not None
            else None
        )
        last_reconcile = existing["last_reconcile_at"]
    with conn:
        conn.execute(
            "INSERT INTO account_sync("
            "id, changes_cursor, gap_json, last_sync_at, "
            "last_reconcile_at, reconcile_json"
            ") VALUES (1, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "changes_cursor = excluded.changes_cursor, "
            "gap_json = excluded.gap_json, "
            "last_sync_at = excluded.last_sync_at, "
            "last_reconcile_at = excluded.last_reconcile_at, "
            "reconcile_json = excluded.reconcile_json",
            (cursor, gap_json, last_sync, last_reconcile, reconcile_json),
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
