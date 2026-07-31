"""Selected-dialog archive backfill (ADR-0068 Phase 1)."""

from __future__ import annotations

import sqlite3
from typing import Any

from telethon import errors as telethon_errors

from tgcli import chatref
from tgcli.archive import scope as scope_mod, store as store_mod
from tgcli.commands.read import _dialog_name, message_to_dict
from tgcli.errors import NotFoundError, PolicyError, RateLimitError


async def resolve_entity(tg, chat: str):
    try:
        return await tg.get_entity(chatref.parse(chat))
    except ValueError:
        raise NotFoundError(f"dialog not found: {chat!r}") from None


async def backfill_dialogs(
    tg,
    conn: sqlite3.Connection,
    chats: list[str],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """Fetch up to ``limit`` recent messages per chat; resume older pages."""
    results: list[dict[str, Any]] = []
    for chat in chats:
        results.append(await backfill_one(tg, conn, chat, limit=limit))
    return results


async def backfill_one(
    tg,
    conn: sqlite3.Connection,
    chat: str,
    *,
    limit: int,
) -> dict[str, Any]:
    entity = await resolve_entity(tg, chat)
    kind = scope_mod.classify_entity(entity)
    peer = scope_mod.peer_id(entity)
    scope_mod.allow_backfill(
        kind, explicitly_scoped=store_mod.in_explicit_scope(conn, peer)
    )
    state = store_mod.get_sync_state(conn, peer)
    offset_id = int(state["oldest_id"]) if state and state.get("oldest_id") else 0
    stored = 0
    inserted = 0
    updated = 0
    ids: list[int] = []
    last_error: str | None = None
    more = False
    try:
        async for message in tg.iter_messages(entity, limit=limit, offset_id=offset_id):
            payload = message_to_dict(message, entity)
            with conn:
                action = store_mod.upsert_message(conn, peer, payload)
            stored += 1
            if action == "inserted":
                inserted += 1
            elif action == "updated":
                updated += 1
            ids.append(int(payload["id"]))
        # If we filled the page, assume there may be older history.
        more = stored >= limit and limit > 0
    except telethon_errors.FloodWaitError as exc:
        last_error = f"FLOOD_WAIT:{exc.seconds}"
        with conn:
            store_mod.upsert_sync_state(
                conn,
                peer,
                oldest_id=min(ids) if ids else (state or {}).get("oldest_id"),
                newest_id=max(ids) if ids else (state or {}).get("newest_id"),
                more=True,
                last_error=last_error,
            )
        raise RateLimitError(
            f"rate limited during archive backfill of {chat!r}",
            retry_after=int(exc.seconds),
        ) from exc
    except Exception as exc:
        last_error = f"{type(exc).__name__}:{exc}"
        with conn:
            store_mod.upsert_sync_state(
                conn,
                peer,
                oldest_id=min(ids) if ids else (state or {}).get("oldest_id"),
                newest_id=max(ids) if ids else (state or {}).get("newest_id"),
                more=True,
                last_error=last_error,
            )
        raise

    with conn:
        store_mod.upsert_sync_state(
            conn,
            peer,
            oldest_id=min(ids) if ids else (state or {}).get("oldest_id"),
            newest_id=max(ids) if ids else (state or {}).get("newest_id"),
            more=more,
            last_error=None,
        )
    return {
        "chat": chat,
        "peer_id": peer,
        "kind": kind,
        "dialog": {"id": entity.id, "name": _dialog_name(entity, chat)},
        "stored": stored,
        "inserted": inserted,
        "updated": updated,
        "more": more,
        "oldest_id": min(ids) if ids else None,
        "newest_id": max(ids) if ids else None,
    }


def validate_limit(limit: int | None, *, default: int, maximum: int) -> int:
    if limit is None:
        return default
    if limit <= 0:
        raise PolicyError("archive backfill --limit must be positive")
    if limit > maximum:
        raise PolicyError(
            f"archive backfill --limit accepts at most {maximum} messages per dialog"
        )
    return limit


def validate_dialogs(chats: list[str], *, maximum: int) -> list[str]:
    if not chats:
        raise PolicyError(
            "archive backfill requires at least one CHAT; "
            "there is no empty-list all-dialogs sentinel"
        )
    cleaned = [chat for chat in chats if chat]
    if len(cleaned) != len(chats) or not cleaned:
        raise PolicyError("archive backfill CHAT values must be non-empty")
    if len(cleaned) > maximum:
        raise PolicyError(
            f"archive backfill accepts at most {maximum} dialogs per invocation"
        )
    return cleaned
