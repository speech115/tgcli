"""Offline archive FTS5 MATCH helpers (ADR-0068)."""

from __future__ import annotations

import re
import sqlite3

from tgcli.archive import peers as peers_mod, store as store_mod
from tgcli.errors import NotFoundError, PolicyError

# FTS5 operators / wildcards — presence means pass-through raw MATCH.
_RAW_MATCH = re.compile(
    r"""[*"]|\b(?:AND|OR|NOT|NEAR)\b|[()]""",
    re.IGNORECASE,
)


def validate_query(query: str | None) -> str:
    if query is None or not str(query).strip():
        raise PolicyError("archive search QUERY must be non-empty")
    return str(query).strip()


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
        if peers_mod.known(conn, peer):
            return peer
        raise NotFoundError(f"chat not in archive store: {chat!r}")

    needle = raw.lstrip("@").casefold()
    for row in peers_mod.list_resolve_rows(conn):
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
