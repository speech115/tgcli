"""Archive scope rows, peer sync_state, and account_sync (ADR-0068/0116)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from tgcli import changes_cursor as cursor_codec


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
    }


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
) -> str | None:
    """Update account sync state without a stale read-modify-write window."""
    if not conn.in_transaction:
        conn.execute("BEGIN IMMEDIATE")
    try:
        existing = read_account_sync(conn)
        now = datetime.now(UTC).isoformat()
        cursor = (
            changes_cursor if changes_cursor is not None else existing["changes_cursor"]
        )
        if scope_channels and cursor is not None:
            decoded = cursor_codec.decode(cursor)
            scoped_peers = {
                int(row["peer_id"])
                for row in conn.execute("SELECT peer_id FROM scope").fetchall()
            }
            cursor = cursor_codec.encode(
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
                json.dumps(
                    existing["reconcile"], ensure_ascii=False, separators=(",", ":")
                )
                if existing["reconcile"] is not None
                else None
            )
            last_reconcile = existing["last_reconcile_at"]
        if clear_private_enum:
            private_enum_json = None
        elif private_enum is not None:
            private_enum_json = json.dumps(
                private_enum, ensure_ascii=False, separators=(",", ":")
            )
        else:
            private_enum_json = (
                json.dumps(
                    existing["private_enum"],
                    ensure_ascii=False,
                    separators=(",", ":"),
                )
                if existing["private_enum"] is not None
                else None
            )
        conn.execute(
            "INSERT INTO account_sync("
            "id, changes_cursor, gap_json, last_sync_at, "
            "last_reconcile_at, reconcile_json, private_enum_json"
            ") VALUES (1, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT(id) DO UPDATE SET "
            "changes_cursor = excluded.changes_cursor, "
            "gap_json = excluded.gap_json, "
            "last_sync_at = excluded.last_sync_at, "
            "last_reconcile_at = excluded.last_reconcile_at, "
            "reconcile_json = excluded.reconcile_json, "
            "private_enum_json = excluded.private_enum_json",
            (
                cursor,
                gap_json,
                last_sync,
                last_reconcile,
                reconcile_json,
                private_enum_json,
            ),
        )
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return cursor
