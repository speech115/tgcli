"""Offline archive FTS5 query building (ADR-0068 thin search)."""

from __future__ import annotations

import re
import sqlite3
from typing import Any

from tgcli.archive import store as store_mod
from tgcli.errors import NotFoundError, PolicyError

DEFAULT_LIMIT = 20
MAX_LIMIT = 50

# FTS5 operators / wildcards — presence means pass-through raw MATCH.
_RAW_MATCH = re.compile(
    r"""[*"]|\b(?:AND|OR|NOT|NEAR)\b|[()]""",
    re.IGNORECASE,
)


def validate_query(query: str | None) -> str:
    if query is None or not str(query).strip():
        raise PolicyError("archive search QUERY must be non-empty")
    return str(query).strip()


def validate_limit(limit: int | None, *, default: int, maximum: int) -> int:
    if limit is None:
        return default
    if limit <= 0:
        raise PolicyError("archive search --limit must be positive")
    if limit > maximum:
        raise PolicyError(f"archive search --limit accepts at most {maximum} hits")
    return limit


def is_raw_match(query: str) -> bool:
    return _RAW_MATCH.search(query) is not None


def build_match(query: str) -> tuple[str, str]:
    """Return ``(match_string, mode)`` where mode is ``exact`` or ``raw``.

    Exact mode is a plain FTS5 MATCH (no auto-prefix). Raw mode passes the
    user string through when it already contains FTS operators such as ``*``.
    Cyrillic yo is folded to ye so ``елка`` hits indexed ``ёлка``.
    """
    mode = "raw" if is_raw_match(query) else "exact"
    return store_mod.fold_yo(query), mode


def resolve_peer_id(conn: sqlite3.Connection, chat: str) -> int:
    raw = chat.strip()
    if not raw:
        raise PolicyError("archive search --chat must be non-empty")
    try:
        peer = int(raw)
    except ValueError:
        peer = None
    if peer is not None:
        if _peer_known(conn, peer):
            return peer
        raise NotFoundError(f"chat not in archive store: {chat!r}")

    needle = raw.lstrip("@").casefold()
    for row in list(store_mod.list_scope(conn)) + store_mod.list_sync_identity(conn):
        candidates = [
            row.get("chat_ref"),
            row.get("username"),
            f"@{row['username']}" if row.get("username") else None,
            str(row["peer_id"]),
        ]
        for candidate in candidates:
            if candidate is None:
                continue
            if str(candidate).lstrip("@").casefold() == needle:
                return int(row["peer_id"])
            if str(candidate) == raw:
                return int(row["peer_id"])
    raise NotFoundError(f"chat not in archive store: {chat!r}")


def _peer_known(conn: sqlite3.Connection, peer_id: int) -> bool:
    if store_mod.in_explicit_scope(conn, peer_id):
        return True
    if store_mod.get_sync_state(conn, peer_id) is not None:
        return True
    row = conn.execute(
        "SELECT 1 FROM messages WHERE peer_id = ? LIMIT 1", (peer_id,)
    ).fetchone()
    return row is not None


def search(
    conn: sqlite3.Connection,
    query: str,
    *,
    chat: str | None = None,
    limit: int = DEFAULT_LIMIT,
) -> dict[str, Any]:
    query = validate_query(query)
    limit = validate_limit(limit, default=DEFAULT_LIMIT, maximum=MAX_LIMIT)
    match, mode = build_match(query)
    peer_id: int | None = None
    if chat is not None:
        peer_id = resolve_peer_id(conn, chat)

    sql = """
        SELECT m.peer_id AS peer_id,
               m.message_id AS message_id,
               m.date AS date,
               m.text AS text,
               COALESCE(s.chat_ref, ss.chat_ref) AS chat_ref,
               COALESCE(s.title, ss.title) AS title,
               COALESCE(s.username, ss.username) AS username,
               t.text AS transcript,
               t.status AS transcript_status
        FROM messages_fts
        JOIN messages AS m
          ON m.peer_id = messages_fts.peer_id
         AND m.message_id = messages_fts.message_id
        LEFT JOIN scope AS s ON s.peer_id = m.peer_id
        LEFT JOIN sync_state AS ss ON ss.peer_id = m.peer_id
        LEFT JOIN transcripts AS t
          ON t.peer_id = m.peer_id AND t.message_id = m.message_id
        WHERE messages_fts MATCH ?
    """
    params: list[Any] = [match]
    if peer_id is not None:
        sql += " AND m.peer_id = ?"
        params.append(peer_id)
    sql += " ORDER BY rank, m.date DESC LIMIT ?"
    params.append(limit)

    rows = conn.execute(sql, params).fetchall()
    hits = [
        {
            "peer_id": int(row["peer_id"]),
            "message_id": int(row["message_id"]),
            "date": row["date"],
            "text": row["text"] or "",
            "transcript": row["transcript"],
            "transcript_status": row["transcript_status"],
            "chat_ref": row["chat_ref"]
            or (f"@{row['username']}" if row["username"] else None),
            "title": row["title"],
        }
        for row in rows
    ]
    sync_rows = store_mod.list_sync_state(conn)
    stale = any(bool(row.get("more")) for row in sync_rows)
    note = "Results cover archived peers only."
    if stale:
        note += " At least one dialog still has more history on Telegram (more=true)."
    return {
        "query": query,
        "match": match,
        "match_mode": mode,
        "limit": limit,
        "chat": chat,
        "peer_id": peer_id,
        "hits": hits,
        "scope": {
            "archived_peers_only": True,
            "stale": stale,
            "note": note,
        },
    }
