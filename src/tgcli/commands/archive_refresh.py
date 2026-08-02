"""Network command wrapper for the bounded archive refresh (ADR-0070)."""

from __future__ import annotations

from pathlib import Path

from tgcli.archive import (
    refresh as refresh_mod,
    store as store_mod,
    sync as sync_mod,
    transcribe as transcribe_mod,
)
from tgcli.config import Config, load_config
from tgcli.errors import NotFoundError, PartialFailure


def _account_dir(alias: str, config: Config) -> Path:
    root = config.archive_root or store_mod.default_archive_root()
    return store_mod.ensure_account_dir(root, alias)


def _open_existing(alias: str, directory: Path):
    path = store_mod.db_path_for(directory)
    if not path.exists():
        raise NotFoundError("archive store is not initialized; run: tg archive init")
    return store_mod.connect(path)


async def refresh(
    tg,
    alias: str,
    *,
    max_events: int | None = None,
    max_dialogs: int | None = None,
    max_media: int | None = None,
    transcribe_limit: int | None = None,
    max_attempts: int | None = None,
    config: Config | None = None,
) -> dict:
    max_events = sync_mod.validate_max_events(
        max_events,
        default=sync_mod.DEFAULT_MAX_CATCHUP_MESSAGES,
        maximum=sync_mod.MAX_CATCHUP_MESSAGES,
    )
    max_dialogs = sync_mod.validate_max_dialogs(
        max_dialogs,
        default=sync_mod.DEFAULT_MAX_CATCHUP_DIALOGS,
        maximum=sync_mod.MAX_CATCHUP_DIALOGS,
    )
    max_media = sync_mod.validate_max_media(
        max_media,
        default=sync_mod.DEFAULT_MAX_MEDIA,
        maximum=sync_mod.MAX_MEDIA,
    )
    transcribe_limit = transcribe_mod.validate_limit(
        transcribe_limit, label="transcribe-limit"
    )
    max_attempts = transcribe_mod.validate_max_attempts(max_attempts)
    cfg = config if config is not None else load_config()
    directory = _account_dir(alias, cfg)
    conn = _open_existing(alias, directory)
    try:
        # A scheduled wake into a cooldown must not even send the get_me
        # RPC to learn who it is — the ledger knows, and the store already
        # bound the account (plan phase 6, review fix M2).
        meta = store_mod.read_meta(conn)
        if refresh_mod.sync_types_cooling(tg):
            user_id = int(meta["account_user_id"])
        else:
            me = await tg.get_me()
            store_mod.require_bound_user(conn, int(me.id), alias)
            user_id = int(me.id)
        data = await refresh_mod.run(
            tg,
            conn,
            account_alias=alias,
            account_user_id=user_id,
            account_dir=directory,
            max_events=max_events,
            max_dialogs=max_dialogs,
            max_media=max_media,
            transcribe_limit=transcribe_limit,
            max_attempts=max_attempts,
        )
    except PartialFailure as exc:
        exc.data["account"] = {"alias": alias, "user_id": user_id}
        exc.data["max_events"] = max_events
        exc.data["max_dialogs"] = max_dialogs
        exc.data["max_media"] = max_media
        exc.data["transcribe_limit"] = transcribe_limit
        exc.data["max_attempts"] = max_attempts
        exc.rows = refresh_rows(exc.data)
        raise
    finally:
        conn.close()
    data["account"] = {"alias": alias, "user_id": user_id}
    data["max_events"] = max_events
    data["max_dialogs"] = max_dialogs
    data["max_media"] = max_media
    data["transcribe_limit"] = transcribe_limit
    data["max_attempts"] = max_attempts
    return data


def refresh_rows(data: dict) -> list[tuple]:
    sync = data.get("sync") or {}
    applied = sync.get("applied") or {}
    media = sync.get("media") or {}
    transcribe = data.get("transcribe") or {}
    refresh_state = data.get("refresh") or {}
    return [
        ("events", applied.get("events", 0)),
        ("media_downloaded", media.get("downloaded", 0)),
        ("transcribed", transcribe.get("transcribed", 0)),
        ("remaining", transcribe.get("remaining", False)),
        ("failure_streak", refresh_state.get("failure_streak", 0)),
        ("notification_sent", refresh_state.get("notification_sent", False)),
    ]
