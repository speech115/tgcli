"""Unified peer-identity reads for archive explore/search (ADR-0116)."""

from __future__ import annotations

import sqlite3
from typing import Any

from tgcli.archive import sync_state as sync_state_mod

# Shared SQL fragments so explore search joins stop duplicating COALESCE sprawl.
IDENTITY_SELECT = """
               COALESCE(s.chat_ref, ss.chat_ref) AS chat_ref,
               COALESCE(s.title, ss.title) AS title,
               COALESCE(s.username, ss.username) AS username,
               COALESCE(s.kind, ss.kind) AS kind
"""

IDENTITY_JOINS = """
        LEFT JOIN scope AS s ON s.peer_id = m.peer_id
        LEFT JOIN sync_state AS ss ON ss.peer_id = m.peer_id
"""


def resolve(conn: sqlite3.Connection, peer_id: int) -> dict[str, Any]:
    """Return display identity for one peer: explicit scope wins over sync_state."""
    row = conn.execute(
        "SELECT chat_ref, title, username, kind FROM scope WHERE peer_id = ?",
        (peer_id,),
    ).fetchone()
    if row is None:
        row = conn.execute(
            "SELECT chat_ref, title, username, kind FROM sync_state WHERE peer_id = ?",
            (peer_id,),
        ).fetchone()
    if row is None:
        return {
            "chat_ref": str(peer_id),
            "title": None,
            "username": None,
            "kind": None,
        }
    return {
        "chat_ref": row["chat_ref"]
        or (f"@{row['username']}" if row["username"] else str(peer_id)),
        "title": row["title"],
        "username": row["username"],
        "kind": row["kind"],
    }


def list_sync_identity(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Identity rows used by offline ``--chat`` resolution (private peers)."""
    return [
        row
        for row in sync_state_mod.list_sync_state(conn)
        if row.get("chat_ref") or row.get("username") or row.get("title")
    ]


def list_resolve_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Scope then sync_state identity rows for offline chat resolution."""
    return list(sync_state_mod.list_scope(conn)) + list_sync_identity(conn)


def known(conn: sqlite3.Connection, peer_id: int) -> bool:
    """True when the peer appears in scope, sync_state, or messages."""
    if sync_state_mod.in_explicit_scope(conn, peer_id):
        return True
    if sync_state_mod.get_sync_state(conn, peer_id) is not None:
        return True
    row = conn.execute(
        "SELECT 1 FROM messages WHERE peer_id = ? LIMIT 1", (peer_id,)
    ).fetchone()
    return row is not None
