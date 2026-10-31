"""Archive delta sync through the update cursor engine (ADR-0068 Phase 3)."""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from telethon import errors as telethon_errors

from tgcli.archive import (
    changes as changes_engine,
    changes_cursor,
    media as media_mod,
    scope as scope_mod,
    store as store_mod,
)
from tgcli.archive.changes_cursor import ChangesCursor
from tgcli.commands import media as media_cmd
from tgcli.commands.read import message_to_dict
from tgcli.errors import PolicyError, RateLimitError
from tgcli.output import note
from tgcli.session import RUN_ABORTING_ERRORS

CATCHUP_LIMIT = 50
RECONCILE_SAMPLE = 5
# Marked channel/supergroup peer ids are at or below this bound.
CHANNEL_PEER_CEILING = -(10**12)


def has_own_updates(entry: dict[str, Any]) -> bool:
    """A scoped channel or supergroup: the peers with their own update stream.

    A basic group is a `group` in scope too, but its updates arrive through
    the common difference; subscribing it fails.
    """
    return int(entry["peer_id"]) <= CHANNEL_PEER_CEILING


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
) -> dict[str, Any]:
    """Apply every event from a difference pass (never truncate).

    Difference events are already paid for on the wire; dropping a tail while
    advancing the changes cursor would silently lose archive history. Caps
    belong on catch-up RPCs, not on local SQLite applies.
    """
    applied: dict[str, Any] = {
        "events": 0,
        "inserted": 0,
        "updated": 0,
        "edits": 0,
        "tombstones": 0,
        "skipped_out_of_scope": 0,
        "channel_activity": 0,
    }
    activity_peers: list[int] = []
    for event in events:
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
                    more=True if state is None else bool(state["more"]),
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
                targets = store_mod.find_message_peers(conn, ids, exclude_channels=True)
            else:
                peer_id = int(peer)
                if not _in_archive_scope(conn, peer_id, None):
                    applied["skipped_out_of_scope"] += 1
                    applied["events"] += 1
                    continue
                targets = [(peer_id, mid) for mid in ids]
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
    message_budget: int,
) -> dict[str, Any]:
    if message_budget <= 0:
        return {"peer_id": peer_id, "stored": 0, "skipped_budget": True}
    state = store_mod.get_sync_state(conn, peer_id)
    min_id = int(state["newest_id"]) if state and state.get("newest_id") else 0
    chat = (state or {}).get("chat_ref") or str(peer_id)
    try:
        entity = await tg.get_entity(peer_id)
    except ValueError:
        return {"peer_id": peer_id, "stored": 0, "error": "unresolvable"}
    except changes_engine.CHANNEL_LOST_ERRORS:
        return {"peer_id": peer_id, "stored": 0, "error": "channelUnavailable"}
    stored = 0
    ids: list[int] = []
    limit = min(CATCHUP_LIMIT, message_budget)
    forward = min_id > 0
    try:
        # With a stored `newest_id`, read oldest first: a page cut short by
        # the limit or a flood ends at a message whose predecessors are all
        # stored, so `newest_id` never skips past a hole. A peer with no
        # history yet keeps its newest page; backfill owns older messages.
        async for message in tg.iter_messages(
            entity, limit=limit, min_id=min_id, reverse=forward
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
        raise RateLimitError(
            f"rate limited during archive sync catch-up of {chat!r}",
            retry_after=seconds,
        ) from exc
    except changes_engine.CHANNEL_LOST_ERRORS:
        # The account can no longer read this channel: keep what was stored
        # and drop the catch-up instead of failing every later sync on it.
        with conn:
            store_mod.upsert_sync_state(
                conn,
                peer_id,
                oldest_id=(state or {}).get("oldest_id"),
                newest_id=max(ids) if ids else (state or {}).get("newest_id"),
                more=bool(state["more"]) if state else False,
                last_error="channelUnavailable",
                touch_sync=True,
            )
        return {"peer_id": peer_id, "stored": stored, "error": "channelUnavailable"}
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
    return {"peer_id": peer_id, "stored": stored, "more": forward and stored >= limit}


def _media_candidates(
    conn: sqlite3.Connection, account_dir: Path, limit: int
) -> list[sqlite3.Row]:
    if limit <= 0:
        return []
    base = (
        "SELECT t.peer_id, t.message_id, t.media_path, t.media_kind, m.payload "
        "FROM transcripts AS t JOIN messages AS m "
        "ON m.peer_id = t.peer_id AND m.message_id = t.message_id "
        "WHERE t.media_kind IN ('voice', 'video_note') "
    )
    order = "ORDER BY m.date DESC, t.peer_id DESC, t.message_id DESC"
    pending = conn.execute(
        base
        + "AND (t.media_path IS NULL OR t.media_path = '') "
        + "AND t.media_status IN ('pending', 'retryable') "
        + "AND t.media_attempts < ? "
        + order
        + " LIMIT ?",
        (media_mod.MAX_MEDIA_ATTEMPTS, limit),
    ).fetchall()
    if len(pending) >= limit:
        return pending

    missing_paths = conn.execute(
        base
        + "AND t.media_path IS NOT NULL AND t.media_path <> '' "
        + "AND t.media_status IN ('pending', 'retryable', 'done') "
        + "AND t.media_attempts < ? "
        + order,
        (media_mod.MAX_MEDIA_ATTEMPTS,),
    ).fetchall()
    for row in missing_paths:
        relative = Path(str(row["media_path"]))
        if not relative.is_absolute() and ".." not in relative.parts:
            if (account_dir / relative).is_file():
                continue
        pending.append(row)
        if len(pending) >= limit:
            break
    return pending


async def fetch_media(
    tg,
    conn: sqlite3.Connection,
    *,
    account_alias: str,
    account_user_id: int,
    account_dir: Path,
    limit: int,
    should_stop: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Download queued voice/video-note media into the account archive.

    The database row is the queue checkpoint; media is published by the
    existing resumable download seam and only then recorded as available.
    An ordinary failed item is retryable until its media attempt cap; a
    permanently unavailable item then becomes terminal.
    """
    downloaded = 0
    skipped = 0
    failed: list[dict[str, Any]] = []
    rows = _media_candidates(conn, account_dir, limit)
    for row in rows:
        if should_stop is not None and should_stop():
            break
        peer_id = int(row["peer_id"])
        message_id = int(row["message_id"])
        try:
            payload = json.loads(row["payload"])
            info = payload.get("media_info") or {}
            media_kind = str(row["media_kind"])
            relative = row["media_path"] or media_mod.media_relative_path(
                peer_id,
                message_id,
                media_kind=media_kind,
                mime=info.get("mime"),
            )
            relative_path = Path(str(relative))
            if relative_path.is_absolute() or ".." in relative_path.parts:
                raise PolicyError("archive media path escapes the account store")
            destination = account_dir / relative_path
            if destination.exists():
                with conn:
                    media_mod.set_media_path(
                        conn,
                        peer_id,
                        message_id,
                        path=str(relative_path),
                        media_kind=media_kind,
                    )
                skipped += 1
                continue
            destination.parent.mkdir(mode=0o700, parents=True, exist_ok=True)
            await media_cmd.download_media(
                tg,
                media_cmd.MediaSource(
                    chat=str(peer_id), message_id=message_id, private_channel_id=None
                ),
                account_alias,
                output=str(destination),
                parallel=1,
            )
            if not destination.is_file():
                raise RuntimeError(
                    "media downloader returned without publishing a file"
                )
            with conn:
                media_mod.set_media_path(
                    conn,
                    peer_id,
                    message_id,
                    path=str(relative_path),
                    media_kind=media_kind,
                )
            downloaded += 1
        except telethon_errors.FloodWaitError as exc:
            seconds = int(exc.seconds)
            raise RateLimitError(
                f"rate limited during archive media fetch of {message_id}",
                retry_after=seconds,
            ) from exc
        except RUN_ABORTING_ERRORS:
            # The governor refusing a cooling request type, a lost connection,
            # a dead session: nothing was learned about this item, so no
            # attempt is recorded.
            raise
        except (telethon_errors.ServerError, telethon_errors.TimedOutError) as exc:
            # Telegram failing, not the file: stop the pass, keep the row's
            # attempts, and let the next run take it again.
            note(
                f"archive sync: media pass stopped at {peer_id}/{message_id}: "
                f"{type(exc).__name__}: {exc}"
            )
            break
        except Exception as exc:
            error = f"{type(exc).__name__}:{exc}"
            with conn:
                media_mod.record_media_failure(conn, peer_id, message_id, error=error)
            failed.append(
                {"peer_id": peer_id, "message_id": message_id, "error": error}
            )
    return {
        "limit": limit,
        "queued": len(rows),
        "downloaded": downloaded,
        "skipped": skipped,
        "failed": failed,
        "remaining": bool(_media_candidates(conn, account_dir, 1)),
    }


async def _ensure_channel_subscriptions(
    tg, conn: sqlite3.Connection, cursor: ChangesCursor
) -> ChangesCursor:
    for entry in store_mod.list_scope(conn):
        if not has_own_updates(entry):
            continue
        peer = int(entry["peer_id"])
        if peer in cursor.channels:
            continue
        try:
            entity = await tg.get_entity(peer)
            pts = await changes_engine._channel_pts(tg, entity)
        except RUN_ABORTING_ERRORS:
            raise
        except Exception as exc:
            note(f"archive sync: skip channel subscribe {peer}: {exc}")
            continue
        cursor = changes_cursor.with_channel(cursor, peer, pts)
        note(f"archive sync: subscribed {peer} at pts {pts}")
    return cursor


async def light_reconcile(
    tg, conn: sqlite3.Connection, *, sample: int = RECONCILE_SAMPLE
) -> dict[str, Any]:
    """Cheap local-vs-Telegram count sample; rotates across in-scope dialogs."""
    dialogs = store_mod.list_scoped_sync_state(conn)
    comparisons: list[dict[str, Any]] = []
    if not dialogs:
        summary = {"sampled": 0, "mismatched": 0, "comparisons": [], "next_offset": 0}
        store_mod.write_account_sync(conn, reconcile=summary)
        return summary
    previous = store_mod.read_account_sync(conn).get("reconcile") or {}
    offset = int(previous.get("next_offset") or 0) % len(dialogs)
    take = min(sample, len(dialogs))
    chosen = [dialogs[(offset + i) % len(dialogs)] for i in range(take)]
    for row in chosen:
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
        except RUN_ABORTING_ERRORS:
            raise
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
    next_offset = (offset + sample) % len(dialogs)
    summary = {
        "sampled": len(comparisons),
        "mismatched": sum(
            1
            for item in comparisons
            if item["telegram"] is not None and item["telegram"] != item["local"]
        ),
        "comparisons": comparisons,
        "next_offset": next_offset,
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
    max_media: int,
    account_alias: str,
    account_dir: Path,
    reconcile: bool = True,
    should_stop: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Apply a full difference pass, then budgeted channel catch-ups.

    ``max_events`` caps catch-up *message fetches* (network), not how many
    difference events are applied locally — those are always applied in full
    before the changes cursor advances.
    """
    account = store_mod.read_account_sync(conn)
    if account["changes_cursor"]:
        cursor = changes_cursor.decode(account["changes_cursor"])
        initialized = False
    else:
        doc = await changes_engine.init_changes(tg, peers=[])
        cursor = changes_cursor.decode(doc["next_cursor"])
        initialized = True
        store_mod.write_account_sync(
            conn, changes_cursor=doc["next_cursor"], clear_gap=True
        )

    cursor = await _ensure_channel_subscriptions(tg, conn, cursor)
    doc, cursor, requests = await changes_engine.once(tg, cursor, private_deletes=True)
    events = list(doc.get("events") or [])
    applied = apply_events(conn, events)
    await _enrich_private_identity(tg, conn, events)
    # Catch-ups an earlier run left unfinished go first, then this pass's
    # activity; whatever does not finish is persisted for the next run, so
    # `remaining: true` stays true until every scoped channel is caught up.
    queue: list[int] = []
    for peer in [*account["pending_catchups"], *applied["activity_peers"]]:
        if peer not in queue and store_mod.in_explicit_scope(conn, peer):
            queue.append(int(peer))
    catchups: list[dict[str, Any]] = []
    unfinished: list[int] = []
    budget = max_events
    stopped = False
    for index, peer in enumerate(queue):
        if index >= max_dialogs:
            unfinished.extend(queue[index:])
            break
        if should_stop is not None and should_stop():
            stopped = True
            unfinished.extend(queue[index:])
            break
        result = await _catch_up_peer(
            tg,
            conn,
            peer,
            account_user_id=account_user_id,
            message_budget=budget,
        )
        catchups.append(result)
        budget = max(0, budget - int(result.get("stored") or 0))
        if result.get("skipped_budget") or result.get("more"):
            unfinished.append(peer)

    if should_stop is not None and should_stop():
        stopped = True
    # Checkpoint before the media tail: its queue lives in the database, so
    # a media rate limit (exit 5) must not make the next run re-apply this
    # difference.
    gap = doc.get("gap")
    encoded = changes_cursor.encode(cursor)
    # A clean pass does not bring lost edits back, and a new gap joins the
    # stored ones: every recorded gap stays until `rebaseline` clears them.
    persisted = store_mod.write_account_sync(
        conn,
        changes_cursor=encoded,
        gap=gap,
        touch_sync=True,
        scope_channels=True,
        pending_catchups=unfinished,
    )
    assert persisted is not None
    encoded = persisted
    media = await fetch_media(
        tg,
        conn,
        account_alias=account_alias,
        account_user_id=account_user_id,
        account_dir=account_dir,
        limit=max_media,
        should_stop=should_stop,
    )
    if should_stop is not None and should_stop():
        stopped = True
    reconcile_data = None
    if reconcile and not stopped:
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
            "received": len(events),
        },
        "catchups": catchups,
        "media": media,
        "gap": gap,
        "skipped": doc.get("skipped") or {},
        "next_cursor": encoded,
        "reconcile": reconcile_data,
        "requests": len(requests),
        "remaining": bool(stopped or unfinished or media["remaining"]),
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
        except RUN_ABORTING_ERRORS:
            raise
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
        if has_own_updates(entry)
    ]
    doc = await changes_engine.init_changes(tg, peers=peers)
    encoded = store_mod.write_account_sync(
        conn,
        changes_cursor=doc["next_cursor"],
        clear_gap=True,
        touch_sync=True,
        scope_channels=True,
    )
    assert encoded is not None
    return {
        "rebaselined": True,
        "next_cursor": encoded,
        "gap": None,
        "gaps": [],
        "peers": peers,
    }
