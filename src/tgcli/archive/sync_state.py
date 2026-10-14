"""Archive scope rows, peer sync_state, and account_sync (ADR-0068/0116)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from tgcli.archive import changes_cursor as cursor_codec


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
    """Atomically remove explicit scope and its cursor subscription."""
    conn.execute("BEGIN IMMEDIATE")
    try:
        row = conn.execute(
            "SELECT peer_id, kind, title, username, chat_ref, added_at "
            "FROM scope WHERE peer_id = ?",
            (peer_id,),
        ).fetchone()
        if row is None:
            conn.commit()
            return None
        encoded = read_account_sync(conn)["changes_cursor"]
        if encoded:
            cursor = cursor_codec.decode(encoded)
            if peer_id in cursor.channels:
                cursor = cursor_codec.without_channel(cursor, peer_id)
                conn.execute(
                    "UPDATE account_sync SET changes_cursor = ? WHERE id = 1",
                    (cursor_codec.encode(cursor),),
                )
        conn.execute("DELETE FROM scope WHERE peer_id = ?", (peer_id,))
        conn.commit()
    except Exception:
        conn.rollback()
        raise
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
    """Record one peer's sync progress in a single statement.

    The stored id range only widens, a None id or identity field keeps the
    stored one, `more` and `last_error` always take the new value, and the
    run stamps `last_sync_at` (touch_sync) or else `last_backfill_at`.
    """
    now = datetime.now(UTC).isoformat()
    conn.execute(
        "INSERT INTO sync_state("
        "peer_id, oldest_id, newest_id, last_backfill_at, last_sync_at, "
        "last_error, more, kind, title, username, chat_ref"
        ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(peer_id) DO UPDATE SET "
        "oldest_id = MIN(COALESCE(oldest_id, excluded.oldest_id), "
        "COALESCE(excluded.oldest_id, oldest_id)), "
        "newest_id = MAX(COALESCE(newest_id, excluded.newest_id), "
        "COALESCE(excluded.newest_id, newest_id)), "
        "last_backfill_at = COALESCE(excluded.last_backfill_at, last_backfill_at), "
        "last_sync_at = COALESCE(excluded.last_sync_at, last_sync_at), "
        "last_error = excluded.last_error, more = excluded.more, "
        "kind = COALESCE(excluded.kind, kind), "
        "title = COALESCE(excluded.title, title), "
        "username = COALESCE(excluded.username, username), "
        "chat_ref = COALESCE(excluded.chat_ref, chat_ref)",
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


def read_account_sync(conn: sqlite3.Connection) -> dict[str, Any]:
    row = conn.execute("SELECT * FROM account_sync WHERE id = 1").fetchone()
    if row is None:
        return {
            "changes_cursor": None,
            "gap": None,
            "last_sync_at": None,
            "last_reconcile_at": None,
            "reconcile": None,
            "private_enum": None,
            "pending_catchups": [],
        }
    gap = None
    if row["gap_json"]:
        gap = json.loads(row["gap_json"])
    reconcile = None
    if row["reconcile_json"]:
        reconcile = json.loads(row["reconcile_json"])
    private_enum = None
    if row["private_enum_json"]:
        private_enum = json.loads(row["private_enum_json"])
    return {
        "changes_cursor": row["changes_cursor"],
        "gap": gap,
        "last_sync_at": row["last_sync_at"],
        "last_reconcile_at": row["last_reconcile_at"],
        "reconcile": reconcile,
        "private_enum": private_enum,
        "pending_catchups": [
            int(peer) for peer in json.loads(row["pending_catchups_json"] or "[]")
        ],
    }


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _scoped_cursor(conn: sqlite3.Connection, encoded: str) -> str:
    """Drop channels that left archive scope from an encoded cursor."""
    decoded = cursor_codec.decode(encoded)
    scoped_peers = {
        int(row["peer_id"])
        for row in conn.execute("SELECT peer_id FROM scope").fetchall()
    }
    return cursor_codec.encode(
        cursor_codec.ChangesCursor(
            pts=decoded.pts,
            qts=decoded.qts,
            date=decoded.date,
            seq=decoded.seq,
            channels={
                peer: pts
                for peer, pts in decoded.channels.items()
                if peer in scoped_peers
            },
        )
    )


def write_account_sync(
    conn: sqlite3.Connection,
    *,
    changes_cursor: str | None = None,
    gap: dict[str, Any] | None = None,
    touch_sync: bool = False,
    reconcile: dict[str, Any] | None = None,
    clear_gap: bool = False,
    scope_channels: bool = False,
    private_enum: dict[str, Any] | None = None,
    clear_private_enum: bool = False,
    pending_catchups: list[int] | None = None,
) -> str | None:
    """Update only the named account_sync columns; return the stored cursor.

    A None argument leaves its column alone; `clear_gap` and
    `clear_private_enum` set theirs to NULL. The read of the stored cursor
    and the write share one IMMEDIATE transaction, so there is no stale
    read-modify-write window.
    """
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    try:
        now = datetime.now(UTC).isoformat()
        conn.execute("INSERT OR IGNORE INTO account_sync(id) VALUES (1)")
        columns: dict[str, Any] = {}
        cursor = changes_cursor
        if scope_channels:
            if cursor is None:
                cursor = _stored_cursor(conn)
            if cursor is not None:
                cursor = _scoped_cursor(conn, cursor)
        if cursor is not None:
            columns["changes_cursor"] = cursor
        if clear_gap:
            columns["gap_json"] = None
        elif gap is not None:
            columns["gap_json"] = _json(gap)
        if touch_sync:
            columns["last_sync_at"] = now
        if reconcile is not None:
            columns["reconcile_json"] = _json(reconcile)
            columns["last_reconcile_at"] = now
        if clear_private_enum:
            columns["private_enum_json"] = None
        elif private_enum is not None:
            columns["private_enum_json"] = _json(private_enum)
        if pending_catchups is not None:
            columns["pending_catchups_json"] = (
                json.dumps(pending_catchups) if pending_catchups else None
            )
        if columns:
            # Column names come from the fixed keys above, never from input.
            assignments = ", ".join(f"{name} = ?" for name in columns)
            conn.execute(
                f"UPDATE account_sync SET {assignments} WHERE id = 1",
                tuple(columns.values()),
            )
        stored = _stored_cursor(conn)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return stored


def _stored_cursor(conn: sqlite3.Connection) -> str | None:
    row = conn.execute(
        "SELECT changes_cursor FROM account_sync WHERE id = 1"
    ).fetchone()
    return None if row is None else row["changes_cursor"]
