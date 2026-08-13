"""Archive message rows, FTS text column, tombstones (ADR-0068/0116)."""

from __future__ import annotations

import json
import sqlite3
from datetime import UTC, datetime
from typing import Any

from tgcli.archive import transcripts as transcripts_mod
from tgcli.archive.schema import TRANSCRIBABLE_MEDIA_KINDS, fold_yo


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
            transcripts_mod.ensure_transcript_queue(
                conn, peer_id, message_id, media_kind=media_kind
            )
        return "inserted"
    if existing["payload"] == body:
        if media_kind in TRANSCRIBABLE_MEDIA_KINDS:
            transcripts_mod.ensure_transcript_queue(
                conn, peer_id, message_id, media_kind=media_kind
            )
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
    transcripts_mod.replace_fts_row(conn, peer_id, message_id, text)
    if media_kind in TRANSCRIBABLE_MEDIA_KINDS:
        transcripts_mod.ensure_transcript_queue(
            conn, peer_id, message_id, media_kind=media_kind
        )
    return "updated"


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
