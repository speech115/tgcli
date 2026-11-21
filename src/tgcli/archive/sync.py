"""Archive sync: the common update cursor plus catch-ups by ``min_id``.

Each run takes one ``getDifference`` pass and applies every event it brings.
A chat the pass cannot follow (``channel_activity``, or every chat after a
``differenceTooLong``) is read forward from its ``newest_id`` instead. Until
that catch-up finishes the chat's ``newest_id`` is its "complete up to"
watermark: events of later passes are stored but do not move it, or a
catch-up from it would skip the stretch it was meant to fill.
"""

from __future__ import annotations

import json
import sqlite3
from collections.abc import Callable
from pathlib import Path
from typing import Any

from telethon import errors as telethon_errors

from tgcli import pace
from tgcli.archive import (
    changes as changes_engine,
    changes_cursor,
    gaps as gaps_mod,
    media as media_mod,
    scope as scope_mod,
    store as store_mod,
)
from tgcli.commands import media as media_cmd
from tgcli.errors import PolicyError, RateLimitError
from tgcli.messages import message_to_dict
from tgcli.output import note
from tgcli.tg_errors import FLOOD_ERRORS, RUN_ABORTING_ERRORS
from tgcli.transfer import DownloadBusyError

CATCHUP_LIMIT = 50


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
    behind: frozenset[int] = frozenset(),
) -> dict[str, Any]:
    """Apply every event from a difference pass (never truncate).

    Difference events are already paid for on the wire; dropping a tail while
    advancing the changes cursor would silently lose archive history. Caps
    belong on catch-up RPCs, not on local SQLite applies. A message in a chat
    that is ``behind`` (waiting for a catch-up) is stored without moving the
    chat's ``newest_id``.
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
                    oldest_id=None if peer_id in behind else int(message["id"]),
                    newest_id=None if peer_id in behind else int(message["id"]),
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
        async for message in tg.iter_messages(
            entity,
            limit=limit,
            min_id=min_id,
            reverse=forward,
            wait_time=pace.PAGE_WAIT_S,
        ):
            payload = message_to_dict(message, entity)
            with conn:
                store_mod.upsert_message(conn, peer_id, payload)
            stored += 1
            ids.append(int(payload["id"]))
    except FLOOD_ERRORS as exc:
        seconds = int(exc.seconds)
        with conn:
            store_mod.upsert_sync_state(
                conn,
                peer_id,
                oldest_id=(state or {}).get("oldest_id"),
                newest_id=max(ids) if ids else (state or {}).get("newest_id"),
                more=bool(state["more"]) if state else False,
                last_error=f"FLOOD_WAIT:{seconds}",
                touch_sync=True,
            )
        raise RateLimitError(
            f"rate limited during archive sync catch-up of {chat!r}",
            retry_after=seconds,
        ) from exc
    except changes_engine.CHANNEL_LOST_ERRORS:
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
    between_files = pace.Pace(pace.MEDIA_INTERVAL_S)
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
            await between_files.wait()
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
        except FLOOD_ERRORS as exc:
            seconds = int(exc.seconds)
            raise RateLimitError(
                f"rate limited during archive media fetch of {message_id}",
                retry_after=seconds,
            ) from exc
        except DownloadBusyError:
            skipped += 1
            continue
        except RUN_ABORTING_ERRORS:
            raise
        except (telethon_errors.ServerError, telethon_errors.TimedOutError) as exc:
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


def _scoped(conn: sqlite3.Connection, peer_id: int) -> bool:
    return peer_id > 0 or store_mod.in_explicit_scope(conn, peer_id)


async def _dialogs_behind(tg, conn: sqlite3.Connection) -> list[int]:
    """Chats in scope whose newest message is past the stored ``newest_id``.

    The recovery for a lost stretch of updates: one GetDialogs page per 100
    chats, then a catch-up for each chat that moved.
    """
    moved: list[int] = []
    async for dialog in pace.paged(tg.iter_dialogs(), pace.DIALOGS_PAGE):
        peer_id = int(dialog.id)
        top = int(getattr(getattr(dialog, "message", None), "id", 0) or 0)
        if not top or not _scoped(conn, peer_id):
            continue
        state = store_mod.get_sync_state(conn, peer_id)
        if top > int((state or {}).get("newest_id") or 0):
            moved.append(peer_id)
    return moved


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
    should_stop: Callable[[], bool] | None = None,
) -> dict[str, Any]:
    """Apply a full difference pass, then budgeted catch-ups.

    ``max_events`` caps catch-up *message fetches* (network), not how many
    difference events are applied locally: those are always applied in full
    before the changes cursor advances.
    """
    account = store_mod.read_account_sync(conn)
    initialized = False
    new_gaps: list[dict[str, Any]] = []
    cursor = None
    if account["changes_cursor"]:
        try:
            cursor = changes_cursor.decode(account["changes_cursor"])
        except PolicyError as exc:
            note(f"archive sync: {exc}; starting a new cursor")
            new_gaps.append(gaps_mod.gap("common", reason="cursorUnreadable"))
    if cursor is None:
        cursor = await changes_engine.init_changes(tg)
        initialized = not new_gaps
    doc, cursor, requests = await changes_engine.once(tg, cursor, private_deletes=True)
    if doc["too_long"]:
        new_gaps.append(gaps_mod.gap("common", reason="differenceTooLong"))
    events = list(doc.get("events") or [])
    activity = [
        int(event["peer"])
        for event in events
        if event.get("type") == "channel_activity"
        and event.get("peer") is not None
        and store_mod.in_explicit_scope(conn, int(event["peer"]))
    ]
    behind = frozenset([*account["pending_catchups"], *activity])
    applied = apply_events(conn, events, behind=behind)
    await _enrich_private_identity(tg, conn, events)
    recovered: list[int] = []
    if new_gaps:
        recovered = await _dialogs_behind(tg, conn)
    queue: list[int] = []
    for peer in [*account["pending_catchups"], *activity, *recovered]:
        if peer not in queue and _scoped(conn, peer):
            queue.append(int(peer))
    catchups: list[dict[str, Any]] = []
    unfinished: list[int] = []
    budget = max_events
    stopped = False
    between_peers = pace.Pace(pace.PAGE_WAIT_S)
    for index, peer in enumerate(queue):
        if index >= max_dialogs:
            unfinished.extend(queue[index:])
            break
        if should_stop is not None and should_stop():
            stopped = True
            unfinished.extend(queue[index:])
            break
        await between_peers.wait()
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
    persisted = store_mod.write_account_sync(
        conn,
        changes_cursor=changes_cursor.encode(cursor),
        gaps=new_gaps,
        clear_gap=not unfinished,
        touch_sync=True,
        pending_catchups=unfinished,
    )
    assert persisted is not None
    gaps = store_mod.read_account_sync(conn)["gaps"]
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
        "recovered": len(recovered),
        "media": media,
        "gap": gaps[-1] if gaps else None,
        "gaps": gaps,
        "skipped": doc.get("skipped") or {},
        "next_cursor": persisted,
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
