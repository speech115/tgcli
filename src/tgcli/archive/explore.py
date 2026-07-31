"""Offline archive search, timeline, and history queries (ADR-0068)."""

from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any
from urllib.parse import quote

from tgcli.archive import search as search_mod
from tgcli.errors import NotFoundError, PolicyError

SEARCH_DEFAULT_LIMIT = 20
SEARCH_MAX_LIMIT = 50
READ_DEFAULT_LIMIT = 20
READ_MAX_LIMIT = 50
MAX_PAGE = 10_000
SORTS = ("relevance", "date")
KINDS = ("text", "photo", "video", "video_note", "audio", "voice", "document")


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def validate_limit(value: int | None, *, default: int, maximum: int, label: str) -> int:
    if value is None:
        return default
    if value <= 0:
        raise PolicyError(f"archive {label} --limit must be positive")
    if value > maximum:
        raise PolicyError(f"archive {label} --limit accepts at most {maximum} hits")
    return value


def validate_page(value: int | None) -> int:
    page = 1 if value is None else value
    if page <= 0:
        raise PolicyError("archive search --page must be positive")
    if page > MAX_PAGE:
        raise PolicyError(f"archive search --page accepts at most {MAX_PAGE}")
    return page


def validate_filter(value: str | None, flag: str) -> str | None:
    if value is None:
        return None
    value = str(value).strip()
    if not value:
        raise PolicyError(f"archive search {flag} must be non-empty")
    return value


def validate_kind(kind: str | None) -> str | None:
    if kind is not None and kind not in KINDS:
        raise PolicyError(f"archive search --kind must be one of: {', '.join(KINDS)}")
    return kind


def validate_sort(sort: str | None) -> str:
    sort = sort or "relevance"
    if sort not in SORTS:
        raise PolicyError(f"archive search --sort must be one of: {', '.join(SORTS)}")
    return sort


def validate_message_id(message_id: int) -> int:
    if message_id <= 0:
        raise PolicyError("archive history MESSAGE_ID must be positive")
    return message_id


def validate_date_range(
    since: datetime | None, until: datetime | None, label: str
) -> None:
    if since is not None and until is not None and since > until:
        raise PolicyError(f"archive {label} --since must not be after --until")


def validate_read_centers(around_id: int | None, around_date: datetime | None) -> None:
    if around_id is not None and around_id <= 0:
        raise PolicyError("archive read --around-id must be positive")
    if around_id is not None and around_date is not None:
        raise PolicyError(
            "archive read accepts only one of --around-id or --around-date"
        )


def tg_link(
    peer_id: int,
    message_id: int,
    *,
    username: str | None = None,
    kind: str | None = None,
) -> str:
    if kind in ("group", "channel"):
        if username:
            return f"tg://resolve?domain={quote(username, safe='')}&post={message_id}"
        peer_ref = str(peer_id)
        if kind == "channel" or peer_ref.startswith("-100"):
            channel_id = peer_ref[4:] if peer_ref.startswith("-100") else peer_ref
            return f"tg://privatepost?channel={channel_id}&post={message_id}"
    if peer_id > 0:
        return f"tg://openmessage?user_id={peer_id}&message_id={message_id}"
    return f"tg://openmessage?chat_id={peer_id}&message_id={message_id}"


def _scope(conn: sqlite3.Connection) -> dict[str, Any]:
    stale = any(bool(row.get("more")) for row in _sync_rows(conn))
    note = "Results cover archived peers only."
    if stale:
        note += " At least one dialog still has more history on Telegram (more=true)."
    return {"archived_peers_only": True, "stale": stale, "note": note}


def _sync_rows(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    return search_mod.store_mod.list_sync_state(conn)


def _payload(value: str | None, peer_id: int, message_id: int) -> dict[str, Any]:
    try:
        data = json.loads(value or "{}")
    except (TypeError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    data.setdefault("id", message_id)
    data.setdefault("peer_id", peer_id)
    return data


def _record(
    payload: dict[str, Any],
    *,
    peer_id: int,
    message_id: int,
    transcript: str | None = None,
    transcript_status: str | None = None,
    username: str | None = None,
    kind: str | None = None,
) -> dict[str, Any]:
    record = dict(payload)
    record["id"] = message_id
    record["peer_id"] = peer_id
    record["transcript"] = transcript
    record["transcript_status"] = transcript_status
    record["tg_link"] = tg_link(peer_id, message_id, username=username, kind=kind)
    return record


def _identity(conn: sqlite3.Connection, peer_id: int) -> dict[str, Any]:
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
        return {"chat_ref": str(peer_id), "title": None, "username": None, "kind": None}
    return {
        "chat_ref": row["chat_ref"]
        or (f"@{row['username']}" if row["username"] else str(peer_id)),
        "title": row["title"],
        "username": row["username"],
        "kind": row["kind"],
    }


def _date_parts(
    since: datetime | None, until: datetime | None
) -> tuple[str, list[Any]]:
    clauses: list[str] = []
    params: list[Any] = []
    if since is not None:
        clauses.append("julianday(m.date) >= julianday(?)")
        params.append(since.isoformat())
    if until is not None:
        clauses.append("julianday(m.date) <= julianday(?)")
        params.append(until.isoformat())
    return (" AND " + " AND ".join(clauses)) if clauses else "", params


def _search_filters(
    conn: sqlite3.Connection,
    *,
    chat: str | None,
    from_user: str | None,
    since: datetime | None,
    until: datetime | None,
    kind: str | None,
    transcripts_only: bool,
) -> tuple[str, list[Any], int | None]:
    peer_id = None
    clauses: list[str] = []
    params: list[Any] = []
    if chat is not None:
        peer_id = search_mod.resolve_peer_id(conn, chat)
        clauses.append("m.peer_id = ?")
        params.append(peer_id)
    if from_user is not None:
        wanted = validate_filter(from_user, "--from") or ""
        if wanted.lstrip("@").isdigit():
            clauses.append("m.from_id = ?")
            params.append(int(wanted.lstrip("@")))
        else:
            wanted = wanted.lstrip("@").casefold()
            clauses.append(
                "(tgcli_casefold(json_extract(m.payload, '$.from.username')) = ? "
                "OR tgcli_casefold(json_extract(m.payload, '$.from.name')) = ?)"
            )
            params.extend((wanted, wanted))
    date_sql, date_params = _date_parts(since, until)
    clauses.append(date_sql[5:] if date_sql else "1 = 1")
    params.extend(date_params)
    if kind == "text":
        clauses.append("COALESCE(json_extract(m.payload, '$.media_kind'), '') = ''")
    elif kind is not None:
        clauses.append("json_extract(m.payload, '$.media_kind') = ?")
        params.append(kind)
    if transcripts_only:
        clauses.append("t.text IS NOT NULL AND t.text != ''")
    return " AND ".join(clauses), params, peer_id


def _search_row(row: sqlite3.Row) -> dict[str, Any]:
    payload = _payload(row["payload"], int(row["peer_id"]), int(row["message_id"]))
    text_snippet = row["text_snippet"] or ""
    transcript_snippet = row["transcript_snippet"] or ""
    fields = []
    if "[[" in text_snippet:
        fields.append("text")
    if "[[" in transcript_snippet:
        fields.append("transcript")
    snippet = transcript_snippet if "transcript" in fields else text_snippet
    return {
        "peer_id": int(row["peer_id"]),
        "message_id": int(row["message_id"]),
        "date": row["date"],
        "text": row["text"] or "",
        "transcript": row["transcript"],
        "transcript_status": row["transcript_status"],
        "chat_ref": row["chat_ref"]
        or (f"@{row['username']}" if row["username"] else None),
        "title": row["title"],
        "kind": payload.get("media_kind"),
        "from_id": payload.get("from", {}).get("id")
        if isinstance(payload.get("from"), dict)
        else row["from_id"],
        "permalink": payload.get("permalink"),
        "tg_link": tg_link(
            int(row["peer_id"]),
            int(row["message_id"]),
            username=row["username"],
            kind=row["kind"],
        ),
        "rank": float(row["score"]),
        "match_fields": fields,
        "snippet": snippet or None,
        "snippet_source": fields[0] if fields else None,
    }


def search(
    conn: sqlite3.Connection,
    query: str,
    *,
    chat: str | None = None,
    from_user: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    kind: str | None = None,
    transcripts_only: bool = False,
    sort: str = "relevance",
    limit: int = SEARCH_DEFAULT_LIMIT,
    page: int = 1,
) -> dict[str, Any]:
    query = search_mod.validate_query(query)
    limit = validate_limit(
        limit, default=SEARCH_DEFAULT_LIMIT, maximum=SEARCH_MAX_LIMIT, label="search"
    )
    page = validate_page(page)
    kind = validate_kind(kind)
    sort = validate_sort(sort)
    match, mode = search_mod.build_match(query)
    where, params, peer_id = _search_filters(
        conn,
        chat=chat,
        from_user=from_user,
        since=since,
        until=until,
        kind=kind,
        transcripts_only=transcripts_only,
    )
    fts_match = f"transcript : ({match})" if transcripts_only else match
    sql = (
        """
        SELECT m.peer_id, m.message_id, m.date, m.from_id, m.text, m.payload,
               COALESCE(s.chat_ref, ss.chat_ref) AS chat_ref,
               COALESCE(s.title, ss.title) AS title,
               COALESCE(s.username, ss.username) AS username,
               COALESCE(s.kind, ss.kind) AS kind,
               t.text AS transcript, t.status AS transcript_status,
               bm25(messages_fts) AS score,
               snippet(messages_fts, 0, '[[', ']]', '…', 12) AS text_snippet,
               snippet(messages_fts, 1, '[[', ']]', '…', 12) AS transcript_snippet
        FROM messages_fts
        JOIN messages AS m ON m.peer_id = messages_fts.peer_id
                          AND m.message_id = messages_fts.message_id
        LEFT JOIN scope AS s ON s.peer_id = m.peer_id
        LEFT JOIN sync_state AS ss ON ss.peer_id = m.peer_id
        LEFT JOIN transcripts AS t ON t.peer_id = m.peer_id
                                  AND t.message_id = m.message_id
        WHERE messages_fts MATCH ? AND """
        + where
    )
    order = (
        "ORDER BY julianday(m.date) DESC, m.message_id DESC, m.peer_id DESC"
        if sort == "date"
        else "ORDER BY score ASC, julianday(m.date) DESC, m.peer_id, m.message_id"
    )
    sql += f" {order} LIMIT ? OFFSET ?"
    try:
        page_rows = conn.execute(
            sql, [fts_match, *params, limit + 1, (page - 1) * limit]
        ).fetchall()
    except sqlite3.OperationalError as exc:
        if "fts5" in str(exc).lower():
            raise PolicyError(
                "archive search QUERY is not a valid FTS5 MATCH expression"
            ) from exc
        raise
    has_more = len(page_rows) > limit
    hits = [_search_row(row) for row in page_rows[:limit]]
    return {
        "query": query,
        "match": match,
        "match_mode": mode,
        "limit": limit,
        "page": page,
        "next_page": page + 1 if has_more else None,
        "has_more": has_more,
        "chat": chat,
        "peer_id": peer_id,
        "filters": {
            "from": from_user,
            "since": _iso(since),
            "until": _iso(until),
            "kind": kind,
            "transcripts_only": transcripts_only,
        },
        "sort": sort,
        "hits": hits,
        "scope": _scope(conn),
    }


def _message_select() -> str:
    return """
        SELECT m.peer_id, m.message_id, m.date, m.from_id, m.text, m.edited_at,
               m.payload, t.text AS transcript, t.status AS transcript_status
        FROM messages AS m
        LEFT JOIN transcripts AS t ON t.peer_id = m.peer_id
                                  AND t.message_id = m.message_id
        WHERE m.peer_id = ?
    """


def _read_rows(
    conn: sqlite3.Connection,
    peer_id: int,
    *,
    since: datetime | None,
    until: datetime | None,
    around_id: int | None,
    around_date: datetime | None,
    limit: int,
) -> list[sqlite3.Row]:
    date_sql, date_params = _date_parts(since, until)
    base = _message_select() + date_sql
    if around_id is not None:
        before = conn.execute(
            base + " AND m.message_id < ? ORDER BY m.message_id DESC LIMIT ?",
            [peer_id, *date_params, around_id, limit // 2],
        ).fetchall()
        after = conn.execute(
            base + " AND m.message_id >= ? ORDER BY m.message_id ASC LIMIT ?",
            [peer_id, *date_params, around_id, limit - limit // 2],
        ).fetchall()
        return sorted(
            [*before, *after], key=lambda row: (row["date"] or "", row["message_id"])
        )
    if around_date is not None:
        center = around_date.isoformat()
        before = conn.execute(
            base + " AND julianday(m.date) <= julianday(?) "
            "ORDER BY julianday(m.date) DESC, m.message_id DESC LIMIT ?",
            [peer_id, *date_params, center, limit // 2],
        ).fetchall()
        after = conn.execute(
            base + " AND julianday(m.date) > julianday(?) "
            "ORDER BY julianday(m.date) ASC, m.message_id ASC LIMIT ?",
            [peer_id, *date_params, center, limit - limit // 2],
        ).fetchall()
        return sorted(
            [*before, *after], key=lambda row: (row["date"] or "", row["message_id"])
        )
    return list(
        reversed(
            conn.execute(
                base + " ORDER BY julianday(m.date) DESC, m.message_id DESC LIMIT ?",
                [peer_id, *date_params, limit],
            ).fetchall()
        )
    )


def read(
    conn: sqlite3.Connection,
    chat: str,
    *,
    since: datetime | None = None,
    until: datetime | None = None,
    around_id: int | None = None,
    around_date: datetime | None = None,
    limit: int = READ_DEFAULT_LIMIT,
) -> dict[str, Any]:
    if not str(chat).strip():
        raise PolicyError("archive read CHAT must be non-empty")
    if around_id is not None and around_id <= 0:
        raise PolicyError("archive read --around-id must be positive")
    if around_id is not None and around_date is not None:
        raise PolicyError(
            "archive read accepts only one of --around-id or --around-date"
        )
    limit = validate_limit(
        limit, default=READ_DEFAULT_LIMIT, maximum=READ_MAX_LIMIT, label="read"
    )
    peer_id = search_mod.resolve_peer_id(conn, chat)
    identity = _identity(conn, peer_id)
    rows = _read_rows(
        conn,
        peer_id,
        since=since,
        until=until,
        around_id=around_id,
        around_date=around_date,
        limit=limit,
    )
    messages = [
        _record(
            _payload(row["payload"], peer_id, int(row["message_id"])),
            peer_id=peer_id,
            message_id=int(row["message_id"]),
            transcript=row["transcript"],
            transcript_status=row["transcript_status"],
            username=identity["username"],
            kind=identity["kind"],
        )
        for row in rows
    ]
    return {
        "chat": chat,
        "peer_id": peer_id,
        "identity": identity,
        "around_id": around_id,
        "around_date": _iso(around_date),
        "since": _iso(since),
        "until": _iso(until),
        "limit": limit,
        "messages": messages,
        "scope": _scope(conn),
    }


def history(conn: sqlite3.Connection, chat: str, message_id: int) -> dict[str, Any]:
    if not str(chat).strip():
        raise PolicyError("archive history CHAT must be non-empty")
    message_id = validate_message_id(message_id)
    peer_id = search_mod.resolve_peer_id(conn, chat)
    current = conn.execute(
        _message_select() + " AND m.message_id = ?", (peer_id, message_id)
    ).fetchone()
    revisions = conn.execute(
        "SELECT edited_at, payload, recorded_at FROM revisions "
        "WHERE peer_id = ? AND message_id = ? ORDER BY recorded_at, edited_at",
        (peer_id, message_id),
    ).fetchall()
    tombstone = conn.execute(
        "SELECT deleted_at FROM tombstones WHERE peer_id = ? AND message_id = ?",
        (peer_id, message_id),
    ).fetchone()
    if current is None and not revisions and tombstone is None:
        raise NotFoundError(f"message not in archive: {chat!r}/{message_id}")
    identity = _identity(conn, peer_id)
    current_record = None
    if current is not None:
        current_record = _record(
            _payload(current["payload"], peer_id, message_id),
            peer_id=peer_id,
            message_id=message_id,
            transcript=current["transcript"],
            transcript_status=current["transcript_status"],
            username=identity["username"],
            kind=identity["kind"],
        )
    return {
        "chat": chat,
        "peer_id": peer_id,
        "message_id": message_id,
        "identity": identity,
        "status": "deleted" if tombstone is not None else "present",
        "current": current_record,
        "revisions": [
            {
                "edited_at": row["edited_at"],
                "recorded_at": row["recorded_at"],
                "message": _record(
                    _payload(row["payload"], peer_id, message_id),
                    peer_id=peer_id,
                    message_id=message_id,
                    username=identity["username"],
                    kind=identity["kind"],
                ),
            }
            for row in revisions
        ],
        "tombstone": None
        if tombstone is None
        else {"deleted_at": tombstone["deleted_at"]},
        "scope": _scope(conn),
    }


def search_rows(data: dict) -> list[tuple]:
    return [
        (
            hit["peer_id"],
            hit["message_id"],
            hit.get("date"),
            hit.get("chat_ref") or hit.get("title"),
            hit.get("text"),
            hit.get("transcript"),
            hit.get("transcript_status"),
            hit.get("tg_link"),
            hit.get("snippet"),
        )
        for hit in data["hits"]
    ]


def read_rows(data: dict) -> list[tuple]:
    return [
        (
            message["id"],
            message.get("date"),
            (message.get("from") or {}).get("name"),
            message.get("text"),
            message.get("tg_link"),
        )
        for message in data["messages"]
    ]


def history_rows(data: dict) -> list[tuple]:
    rows = [("status", data["status"]), ("message_id", data["message_id"])]
    if data.get("current") is not None:
        rows.append(("current", data["current"].get("text")))
    rows.extend(
        [
            ("revisions", len(data["revisions"])),
            ("deleted_at", (data.get("tombstone") or {}).get("deleted_at")),
        ]
    )
    return rows
