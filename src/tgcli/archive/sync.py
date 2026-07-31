"""Archive delta sync via ``tg changes`` cursor (ADR-0068 Phase 3)."""

from __future__ import annotations

import sqlite3
from typing import Any

from telethon import errors as telethon_errors

from tgcli import changes_cursor
from tgcli.archive import (
    scope as scope_mod,
    store as store_mod,
)
from tgcli.changes_cursor import ChangesCursor
from tgcli.clone import flood
from tgcli.commands import changes as changes_cmd
from tgcli.commands.read import message_to_dict
from tgcli.errors import PolicyError, RateLimitError
from tgcli.output import note

DEFAULT_MAX_EVENTS = 500
MAX_EVENTS = 5000
DEFAULT_MAX_CATCHUP_DIALOGS = 20
MAX_CATCHUP_DIALOGS = 50
CATCHUP_LIMIT = 50
RECONCILE_SAMPLE = 5


def validate_max_events(value: int | None, *, default: int, maximum: int) -> int:
    if value is None:
        return default
    if value <= 0:
        raise PolicyError("archive sync --max-events must be positive")
    if value > maximum:
        raise PolicyError(f"archive sync --max-events accepts at most {maximum}")
    return value


def validate_max_dialogs(value: int | None, *, default: int, maximum: int) -> int:
    if value is None:
        return default
    if value <= 0:
        raise PolicyError("archive sync --max-dialogs must be positive")
    if value > maximum:
        raise PolicyError(f"archive sync --max-dialogs accepts at most {maximum}")
    return value


def _in_archive_scope(conn: sqlite3.Connection, peer_id: int, kind: str | None) -> bool:
    if kind == "user":
        return True
    if store_mod.in_explicit_scope(conn, peer_id):
        return True
    state = store_mod.get_sync_state(conn, peer_id)
    return state is not None and state.get("kind") == "user"


def apply_events(
    conn: sqlite3.Connection,
    events: list[dict[str, Any]],
    *,
    max_events: int,
) -> dict[str, Any]:
    """Apply a bounded event list; returns counters + truncated flag."""
    applied = {
        "events": 0,
        "inserted": 0,
        "updated": 0,
        "edits": 0,
        "tombstones": 0,
        "skipped_out_of_scope": 0,
        "channel_activity": 0,
        "truncated": False,
    }
    activity_peers: list[int] = []
    for event in events:
        if applied["events"] >= max_events:
            applied["truncated"] = True
            break
        etype = event.get("type")
        if etype in ("message_new", "message_edit"):
            peer = event.get("peer")
            message = event.get("message") or {}
            if (
                peer is None
                or not isinstance(message, dict)
                or message.get("id") is None
            ):
                continue
            peer_id = int(peer)
            kind = "user" if peer_id > 0 else None
            if not _in_archive_scope(conn, peer_id, kind):
                applied["skipped_out_of_scope"] += 1
                applied["events"] += 1
                continue
            with conn:
                action = store_mod.upsert_message(conn, peer_id, message)
                state = store_mod.get_sync_state(conn, peer_id)
                store_mod.upsert_sync_state(
                    conn,
                    peer_id,
                    oldest_id=int(message["id"]),
                    newest_id=int(message["id"]),
                    more=bool(state["more"]) if state else False,
                    last_error=None,
                    touch_sync=True,
                    kind=(state or {}).get("kind") or kind,
                    title=(state or {}).get("title"),
                    username=(state or {}).get("username"),
                    chat_ref=(state or {}).get("chat_ref") or str(peer_id),
                )
            applied["events"] += 1
            if action == "inserted":
                applied["inserted"] += 1
            elif action == "updated":
                applied["updated"] += 1
                # Difference may deliver an edit as message_new with a new body;
                # upsert_message already appended a revision for the prior payload.
                applied["edits"] += 1
            elif etype == "message_edit":
                applied["edits"] += 1
            continue
        if etype == "message_delete":
            peer = event.get("peer")
            ids = [int(mid) for mid in (event.get("ids") or [])]
            targets: list[tuple[int, int]]
            if peer is None:
                targets = store_mod.find_message_peers(conn, ids)
            else:
                targets = [(int(peer), mid) for mid in ids]
            with conn:
                for peer_id, mid in targets:
                    if store_mod.insert_tombstone(conn, peer_id, mid):
                        applied["tombstones"] += 1
            applied["events"] += 1
            continue
        if etype == "channel_activity":
            peer = event.get("peer")
            if peer is not None and store_mod.in_explicit_scope(conn, int(peer)):
                activity_peers.append(int(peer))
                applied["channel_activity"] += 1
            else:
                applied["skipped_out_of_scope"] += 1
            applied["events"] += 1
            continue
        applied["events"] += 1
    applied["activity_peers"] = activity_peers
    return applied


async def _catch_up_peer(
    tg,
    conn: sqlite3.Connection,
    peer_id: int,
    *,
    account_user_id: int,
    budget: flood.WaitBudget,
) -> dict[str, Any]:
    state = store_mod.get_sync_state(conn, peer_id)
    min_id = int(state["newest_id"]) if state and state.get("newest_id") else 0
    chat = (state or {}).get("chat_ref") or str(peer_id)
    try:
        entity = await tg.get_entity(peer_id)
    except ValueError:
        return {"peer_id": peer_id, "stored": 0, "error": "unresolvable"}
    stored = 0
    ids: list[int] = []
    try:
        async for message in tg.iter_messages(
            entity, limit=CATCHUP_LIMIT, min_id=min_id
        ):
            payload = message_to_dict(message, entity)
            with conn:
                store_mod.upsert_message(conn, peer_id, payload)
            stored += 1
            ids.append(int(payload["id"]))
    except telethon_errors.FloodWaitError as exc:
        seconds = int(exc.seconds)
        with conn:
            store_mod.upsert_sync_state(
                conn,
                peer_id,
                oldest_id=(state or {}).get("oldest_id"),
                newest_id=max(ids) if ids else (state or {}).get("newest_id"),
                more=True,
                last_error=f"FLOOD_WAIT:{seconds}",
                touch_sync=True,
            )
        from tgcli.clone import cooldown as cooldown_mod

        cooldown_mod.arm_account(account_user_id, seconds)
        raise RateLimitError(
            f"rate limited during archive sync catch-up of {chat!r}",
            retry_after=seconds,
        ) from exc
    kind = scope_mod.classify_entity(entity)
    with conn:
        store_mod.upsert_sync_state(
            conn,
            peer_id,
            oldest_id=(state or {}).get("oldest_id"),
            newest_id=max(ids) if ids else (state or {}).get("newest_id"),
            more=bool(state["more"]) if state else False,
            last_error=None,
            touch_sync=True,
            kind=kind,
            title=scope_mod.entity_title(entity),
            username=getattr(entity, "username", None),
            chat_ref=chat,
        )
    return {"peer_id": peer_id, "stored": stored}


async def _ensure_channel_subscriptions(
    tg, conn: sqlite3.Connection, cursor: ChangesCursor
) -> ChangesCursor:
    for entry in store_mod.list_scope(conn):
        if entry["kind"] not in ("channel", "group"):
            continue
        peer = int(entry["peer_id"])
        if peer in cursor.channels:
            continue
        try:
            entity = await tg.get_entity(peer)
            pts = await changes_cmd._channel_pts(tg, entity)
        except Exception as exc:
            note(f"archive sync: skip channel subscribe {peer}: {exc}")
            continue
        cursor = changes_cursor.with_channel(cursor, peer, pts)
        note(f"archive sync: subscribed {peer} at pts {pts}")
    return cursor


async def light_reconcile(
    tg, conn: sqlite3.Connection, *, sample: int = RECONCILE_SAMPLE
) -> dict[str, Any]:
    """Cheap local-vs-Telegram count sample for a few tracked dialogs."""
    dialogs = store_mod.list_sync_state(conn)[:sample]
    comparisons: list[dict[str, Any]] = []
    for row in dialogs:
        peer = int(row["peer_id"])
        local = int(
            conn.execute(
                "SELECT COUNT(*) FROM messages WHERE peer_id = ?", (peer,)
            ).fetchone()[0]
        )
        telegram_total = None
        error = None
        try:
            entity = await tg.get_entity(peer)
            result = await tg.get_messages(entity, limit=0)
            telegram_total = int(getattr(result, "total", 0) or 0)
        except Exception as exc:
            error = f"{type(exc).__name__}:{exc}"
        comparisons.append(
            {
                "peer_id": peer,
                "local": local,
                "telegram": telegram_total,
                "error": error,
            }
        )
    summary = {
        "sampled": len(comparisons),
        "mismatched": sum(
            1
            for item in comparisons
            if item["telegram"] is not None and item["telegram"] != item["local"]
        ),
        "comparisons": comparisons,
    }
    store_mod.write_account_sync(conn, reconcile=summary)
    return summary


async def sync_archive(
    tg,
    conn: sqlite3.Connection,
    *,
    account_user_id: int,
    max_events: int,
    max_dialogs: int,
    budget: flood.WaitBudget | None = None,
    reconcile: bool = True,
) -> dict[str, Any]:
    run_budget = budget if budget is not None else flood.WaitBudget()
    account = store_mod.read_account_sync(conn)
    if account["changes_cursor"]:
        cursor = changes_cursor.decode(account["changes_cursor"])
        initialized = False
    else:
        doc = await changes_cmd.init_changes(tg, peers=[])
        cursor = changes_cursor.decode(doc["next_cursor"])
        initialized = True
        store_mod.write_account_sync(
            conn, changes_cursor=doc["next_cursor"], clear_gap=True
        )

    cursor = await _ensure_channel_subscriptions(tg, conn, cursor)
    doc, cursor, requests = await changes_cmd.once(tg, cursor, private_deletes=True)
    applied = apply_events(conn, doc.get("events") or [], max_events=max_events)
    await _enrich_private_identity(tg, conn, doc.get("events") or [])
    catchups: list[dict[str, Any]] = []
    for peer in (applied.get("activity_peers") or [])[:max_dialogs]:
        catchups.append(
            await _catch_up_peer(
                tg,
                conn,
                peer,
                account_user_id=account_user_id,
                budget=run_budget,
            )
        )

    gap = doc.get("gap")
    encoded = changes_cursor.encode(cursor)
    store_mod.write_account_sync(
        conn,
        changes_cursor=encoded,
        gap=gap,
        touch_sync=True,
        clear_gap=gap is None,
    )
    reconcile_data = None
    if reconcile:
        reconcile_data = await light_reconcile(tg, conn)

    return {
        "initialized": initialized,
        "applied": {
            "events": applied["events"],
            "inserted": applied["inserted"],
            "updated": applied["updated"],
            "edits": applied["edits"],
            "tombstones": applied["tombstones"],
            "skipped_out_of_scope": applied["skipped_out_of_scope"],
            "channel_activity": applied["channel_activity"],
            "truncated": applied["truncated"],
        },
        "catchups": catchups,
        "gap": gap,
        "skipped": doc.get("skipped") or {},
        "next_cursor": encoded,
        "reconcile": reconcile_data,
        "requests": len(requests),
    }


async def _enrich_private_identity(
    tg, conn: sqlite3.Connection, events: list[dict[str, Any]]
) -> None:
    peers = {
        int(event["peer"])
        for event in events
        if event.get("type") in ("message_new", "message_edit")
        and isinstance(event.get("peer"), int)
        and int(event["peer"]) > 0
    }
    for peer_id in peers:
        state = store_mod.get_sync_state(conn, peer_id)
        if state is None:
            continue
        if state.get("username") and state.get("title"):
            continue
        try:
            entity = await tg.get_entity(peer_id)
        except Exception:
            continue
        username = getattr(entity, "username", None)
        with conn:
            store_mod.upsert_sync_state(
                conn,
                peer_id,
                oldest_id=state.get("oldest_id"),
                newest_id=state.get("newest_id"),
                more=bool(state.get("more")),
                last_error=state.get("last_error"),
                touch_sync=True,
                kind="user",
                title=scope_mod.entity_title(entity),
                username=username,
                chat_ref=f"@{username}" if username else str(peer_id),
            )


async def rebaseline(tg, conn: sqlite3.Connection) -> dict[str, Any]:
    """Explicitly re-init the account changes cursor and clear a recorded gap."""
    peers = [
        entry.get("chat_ref") or str(entry["peer_id"])
        for entry in store_mod.list_scope(conn)
        if entry["kind"] in ("channel", "group")
    ]
    # Prefer marked peer ids for GetFullChannel; chat_ref may be a username.
    doc = await changes_cmd.init_changes(tg, peers=peers)
    store_mod.write_account_sync(
        conn,
        changes_cursor=doc["next_cursor"],
        clear_gap=True,
        touch_sync=True,
    )
    return {
        "rebaselined": True,
        "next_cursor": doc["next_cursor"],
        "gap": None,
        "peers": peers,
    }
