"""Durable private-dialog enumeration resume token (ADR-0118)."""

from __future__ import annotations

import sqlite3
from datetime import UTC, datetime
from typing import Any

from telethon import utils
from telethon.tl import types

from tgcli.archive import scope as scope_mod, store as store_mod
from tgcli.errors import PolicyError


def cursor_from_dialog(dialog) -> dict[str, Any]:
    """Build the durable GetDialogs offset token for one walked dialog."""
    entity = dialog.entity
    input_peer = utils.get_input_peer(entity)
    message = getattr(dialog, "message", None)
    message_id = int(getattr(message, "id", 0) or 0)
    date = getattr(dialog, "date", None) or getattr(message, "date", None)
    if date is None:
        date = datetime.now(UTC)
    date_text = date.isoformat() if hasattr(date, "isoformat") else str(date)
    if isinstance(input_peer, types.InputPeerUser):
        kind, peer_key, access_hash = (
            "user",
            int(input_peer.user_id),
            int(input_peer.access_hash),
        )
    elif isinstance(input_peer, types.InputPeerChat):
        kind, peer_key, access_hash = "chat", int(input_peer.chat_id), None
    elif isinstance(input_peer, types.InputPeerChannel):
        kind, peer_key, access_hash = (
            "channel",
            int(input_peer.channel_id),
            int(input_peer.access_hash),
        )
    else:
        kind, peer_key, access_hash = (
            "user",
            int(getattr(entity, "id")),
            int(getattr(entity, "access_hash", 0) or 0),
        )
    return {
        "kind": kind,
        "id": peer_key,
        "access_hash": access_hash,
        "message_id": message_id,
        "date": date_text,
    }


def input_peer_from_cursor(cursor: dict[str, Any]):
    kind = cursor.get("kind")
    peer_key = int(cursor["id"])
    access_hash = int(cursor["access_hash"] or 0) if cursor.get("access_hash") else 0
    if kind == "user":
        return types.InputPeerUser(peer_key, access_hash)
    if kind == "chat":
        return types.InputPeerChat(peer_key)
    if kind == "channel":
        return types.InputPeerChannel(peer_key, access_hash)
    raise PolicyError(f"unsupported private enum cursor kind: {kind!r}")


def persist(conn: sqlite3.Connection, cursor: dict[str, Any] | None) -> None:
    """Replace the private-enum column via the account_sync write seam."""
    if cursor is None:
        store_mod.write_account_sync(conn, clear_private_enum=True)
    else:
        store_mod.write_account_sync(conn, private_enum=cursor)


def advance_past(conn: sqlite3.Connection, cursor: dict[str, Any] | None) -> None:
    """Persist resume after a private dialog finishes (more=false)."""
    if cursor is not None:
        persist(conn, cursor)


def advance_completed(
    conn: sqlite3.Connection,
    dialogs: list[dict[str, Any]],
    cursors: list[dict[str, Any]],
) -> None:
    if len(dialogs) != len(cursors):
        raise PolicyError(
            "private enum advance requires matching dialog and cursor counts"
        )
    for item, cursor in zip(dialogs, cursors, strict=True):
        if not item.get("more", True):
            advance_past(conn, cursor)


async def enumerate_private_dialogs(
    tg,
    conn: sqlite3.Connection,
    *,
    max_dialogs: int,
    skip_complete: bool = True,
) -> tuple[list[str], int, list[dict[str, Any]]]:
    """Return private chat refs under ``max_dialogs`` with resume cursors.

    Resumes ``iter_dialogs`` from the durable account_sync private-enum token
    so job quanta do not re-walk a completed head (ADR-0118).
    """
    refs: list[str] = []
    ref_cursors: list[dict[str, Any]] = []
    skipped = 0
    resume = store_mod.read_account_sync(conn).get("private_enum")
    kwargs: dict[str, Any] = {}
    if resume:
        try:
            kwargs = {
                "offset_date": datetime.fromisoformat(str(resume["date"])),
                "offset_id": int(resume["message_id"]),
                "offset_peer": input_peer_from_cursor(resume),
            }
        except (KeyError, TypeError, ValueError, PolicyError):
            resume = None
            persist(conn, None)
    # safe_walked advances only past dialogs we may skip forever (non-private
    # or completed). Once any incomplete private is queued in refs, freeze it
    # so max_dialogs lookahead cannot persist a later skip and orphan the
    # still-pending peer (ADR-0118 decision 2 / thermos Wave E).
    safe_walked: dict[str, Any] | None = resume
    async for dialog in tg.iter_dialogs(**kwargs):
        candidate = cursor_from_dialog(dialog)
        entity = dialog.entity
        try:
            kind = scope_mod.classify_entity(entity)
        except PolicyError:
            if not refs:
                safe_walked = candidate
            continue
        if kind != "user":
            if not refs:
                safe_walked = candidate
            continue
        peer = scope_mod.peer_id(entity)
        state = store_mod.get_sync_state(conn, peer)
        if (
            skip_complete
            and state is not None
            and state.get("last_backfill_at") is not None
            and not state.get("more", True)
        ):
            skipped += 1
            if not refs:
                safe_walked = candidate
            continue
        username = getattr(entity, "username", None)
        refs.append(f"@{username}" if username else str(peer))
        ref_cursors.append(candidate)
        if len(refs) >= max_dialogs:
            break
    else:
        if not refs:
            persist(conn, None)
            return refs, skipped, ref_cursors
    if safe_walked is not None and safe_walked != resume:
        persist(conn, safe_walked)
    return refs, skipped, ref_cursors
