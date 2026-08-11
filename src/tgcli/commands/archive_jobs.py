"""Archive-owned workload quanta for the ADR-0087 jobs runner."""

from __future__ import annotations

import hashlib
import json

from tgcli.archive import (
    backfill as backfill_mod,
    scope as scope_mod,
    store as store_mod,
    sync as sync_mod,
)
from tgcli.commands import archive as archive_cmd
from tgcli.config import Config
from tgcli.errors import NotFoundError


def _open_existing(alias: str, config: Config | None):
    path = archive_cmd.db_path(alias, config)
    if not path.exists():
        raise NotFoundError("archive store is not initialized; run: tg archive init")
    return store_mod.connect(path)


def progress_token(alias: str, config: Config | None = None) -> dict:
    """Compact durable archive progress, for scheduler error classification."""
    conn = _open_existing(alias, config)
    try:
        store_mod.require_bound_alias(conn, alias)
        account = store_mod.read_account_sync(conn)
        counts = store_mod.counts(conn)
        media_ready = int(
            conn.execute(
                "SELECT COUNT(*) FROM transcripts "
                "WHERE media_path IS NOT NULL AND media_path <> ''"
            ).fetchone()[0]
        )
        cursor_payload = json.dumps(
            [
                tuple(row)
                for row in conn.execute(
                    "SELECT peer_id, oldest_id, newest_id, more "
                    "FROM sync_state ORDER BY peer_id"
                )
            ],
            separators=(",", ":"),
        )
        return {
            "changes_cursor": account["changes_cursor"],
            "dialog_cursors": hashlib.sha256(cursor_payload.encode()).hexdigest(),
            "media_ready": media_ready,
            "messages": counts["messages"],
            "revisions": counts["revisions"],
            "tombstones": counts["tombstones"],
        }
    finally:
        conn.close()


async def backfill_quantum(
    tg,
    alias: str,
    *,
    chats: list[str],
    private: bool,
    limit: int,
    config: Config | None = None,
) -> dict:
    """Backfill one incomplete dialog using the archive's own checkpoint."""
    me = await tg.get_me()
    conn = _open_existing(alias, config)
    try:
        store_mod.require_bound_user(conn, int(me.id), alias)
        skipped_complete = 0
        if private:
            pending, skipped_complete = await backfill_mod.enumerate_private_dialogs(
                tg, conn, max_dialogs=2, skip_complete=True
            )
        else:
            pending = []
            for chat in backfill_mod.validate_dialogs(
                chats, maximum=archive_cmd.MAX_BACKFILL_DIALOGS
            ):
                entity = await backfill_mod.resolve_entity(tg, chat)
                state = store_mod.get_sync_state(conn, scope_mod.peer_id(entity))
                complete = (
                    state is not None
                    and state.get("last_backfill_at") is not None
                    and not state.get("more", True)
                )
                if complete:
                    skipped_complete += 1
                else:
                    pending.append(chat)
        selected = pending[:1]
        dialogs, stop_reason = await backfill_mod.backfill_dialogs(
            tg,
            conn,
            selected,
            limit=limit,
            account_user_id=int(me.id),
        )
        media = await sync_mod.fetch_media(
            tg,
            conn,
            account_alias=alias,
            account_user_id=int(me.id),
            account_dir=archive_cmd.account_dir(alias, config),
            limit=archive_cmd.DEFAULT_SYNC_MEDIA,
        )
        remaining = bool(
            len(pending) > 1
            or any(item["more"] for item in dialogs)
            or stop_reason is not None
            or media["remaining"]
        )
        data = {
            "mode": "private" if private else "chats",
            "limit": limit,
            "dialogs": dialogs,
            "stored": sum(item["stored"] for item in dialogs),
            "skipped_complete": skipped_complete,
            "media": media,
            "remaining": remaining,
            "account": {"alias": alias, "user_id": int(me.id)},
        }
        if stop_reason is not None:
            data["stop_reason"] = stop_reason
        return data
    finally:
        conn.close()
