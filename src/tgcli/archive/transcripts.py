"""Archive transcript queue and FTS transcript column (ADR-0068/0116)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

from tgcli.archive.schema import NO_TRANSCRIPT_MARKER, fold_yo
from tgcli.errors import PolicyError


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


def replace_fts_row(
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
        "updated_at, media_path, media_kind, media_attempts, media_status, last_error"
        ") VALUES (?, ?, NULL, NULL, NULL, 'pending', 0, ?, NULL, ?, 0, "
        "'pending', NULL) "
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
        "media_attempts": int(row["media_attempts"]),
        "media_status": str(row["media_status"]),
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
        "AND t.media_status = 'done' AND t.media_path IS NOT NULL "
        "AND t.attempts < ? "
        "ORDER BY m.date DESC, t.peer_id DESC, t.message_id DESC LIMIT ?",
        (max_attempts, limit),
    ).fetchall()
    return [_transcript_row(row) | {"date": row["date"]} for row in rows]


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
    replace_fts_row(
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
    replace_fts_row(
        conn,
        peer_id,
        message_id,
        message["text"] if message is not None else "",
    )


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
        "SELECT peer_id, message_id, status, attempts, media_attempts, media_status, "
        "updated_at, last_error "
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
            "media_attempts": int(row["media_attempts"]),
            "media_status": str(row["media_status"]),
            "updated_at": str(row["updated_at"]),
            "error": str(row["last_error"]),
        }
        for row in rows
    ]
