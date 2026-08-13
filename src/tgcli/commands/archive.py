"""`tg archive` surface (ADR-0068)."""

from __future__ import annotations

from pathlib import Path

from tgcli.archive import (
    backfill as backfill_mod,
    explore as explore_mod,
    scope as scope_mod,
    store as store_mod,
    sync as sync_mod,
    transcribe as transcribe_mod,
)
from tgcli.config import Config, load_config, resolve_account
from tgcli.errors import NotFoundError, PolicyError
from tgcli.session import state_dir

DEFAULT_BACKFILL_LIMIT = 100
MAX_BACKFILL_LIMIT = 1000
MAX_BACKFILL_DIALOGS = 20
DEFAULT_PRIVATE_DIALOGS = 20
MAX_PRIVATE_DIALOGS = 100
DEFAULT_SEARCH_LIMIT = explore_mod.SEARCH_DEFAULT_LIMIT
MAX_SEARCH_LIMIT = explore_mod.SEARCH_MAX_LIMIT
DEFAULT_READ_LIMIT = explore_mod.READ_DEFAULT_LIMIT
MAX_READ_LIMIT = explore_mod.READ_MAX_LIMIT
DEFAULT_SYNC_EVENTS = sync_mod.DEFAULT_MAX_CATCHUP_MESSAGES
MAX_SYNC_EVENTS = sync_mod.MAX_CATCHUP_MESSAGES
DEFAULT_SYNC_DIALOGS = sync_mod.DEFAULT_MAX_CATCHUP_DIALOGS
MAX_SYNC_DIALOGS = sync_mod.MAX_CATCHUP_DIALOGS
DEFAULT_SYNC_MEDIA = sync_mod.DEFAULT_MAX_MEDIA
MAX_SYNC_MEDIA = sync_mod.MAX_MEDIA


def archive_root(config: Config | None = None) -> Path:
    cfg = config if config is not None else load_config()
    if cfg.archive_root is not None:
        root = cfg.archive_root
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        try:
            if root.stat().st_mode & 0o777 != 0o700:
                root.chmod(0o700)
        except OSError:
            pass
        return root
    return store_mod.default_archive_root()


def account_dir(alias: str, config: Config | None = None) -> Path:
    return store_mod.ensure_account_dir(archive_root(config), alias)


def db_path(alias: str, config: Config | None = None) -> Path:
    return store_mod.db_path_for(account_dir(alias, config))


def _open_existing(alias: str, config: Config | None = None):
    path = db_path(alias, config)
    if not path.exists():
        raise NotFoundError("archive store is not initialized; run: tg archive init")
    return store_mod.connect(path)


def _offline(alias: str, operation, *args, config: Config | None = None, **kwargs):
    conn = _open_existing(alias, config)
    try:
        store_mod.require_bound_alias(conn, alias)
        data = operation(conn, *args, **kwargs)
    finally:
        conn.close()
    data["account"] = {"alias": alias}
    return data


async def init_archive(tg, alias: str, config: Config | None = None) -> dict:
    me = await tg.get_me()
    if me is None or getattr(me, "id", None) is None:
        raise PolicyError("archive init requires an authorized session")
    directory = account_dir(alias, config)
    path = store_mod.db_path_for(directory)
    conn = store_mod.connect(path)
    try:
        created = store_mod.ensure_meta(
            conn, account_user_id=int(me.id), account_alias=alias
        )
        meta = store_mod.read_meta(conn)
    finally:
        conn.close()
    return {
        "created": created,
        "path": str(path),
        "account": {"alias": meta["account_alias"], "user_id": meta["account_user_id"]},
        "schema_version": store_mod.SCHEMA_VERSION,
    }


async def add_chat(tg, alias: str, chat: str, config: Config | None = None) -> dict:
    me = await tg.get_me()
    conn = _open_existing(alias, config)
    try:
        store_mod.require_bound_user(conn, int(me.id), alias)
        entity = await backfill_mod.resolve_entity(tg, chat)
        kind = scope_mod.classify_entity(entity)
        scope_mod.require_explicit_kind(kind)
        entry = store_mod.add_scope(
            conn,
            peer_id=scope_mod.peer_id(entity),
            kind=kind,
            title=scope_mod.entity_title(entity),
            username=getattr(entity, "username", None),
            chat_ref=chat,
        )
    finally:
        conn.close()
    return {"added": entry}


async def remove_chat(tg, alias: str, chat: str, config: Config | None = None) -> dict:
    me = await tg.get_me()
    conn = _open_existing(alias, config)
    try:
        store_mod.require_bound_user(conn, int(me.id), alias)
        entity = await backfill_mod.resolve_entity(tg, chat)
        peer = scope_mod.peer_id(entity)
        removed = store_mod.remove_scope(conn, peer)
        if removed is None:
            raise NotFoundError(f"chat not in archive scope: {chat!r}")
    finally:
        conn.close()
    return {"removed": removed}


def list_scope(alias: str, config: Config | None = None) -> dict:
    conn = _open_existing(alias, config)
    try:
        store_mod.require_bound_alias(conn, alias)
        explicit = store_mod.list_scope(conn)
    finally:
        conn.close()
    return {
        "account": {"alias": alias},
        "standing": {
            "kind": "private",
            "description": "private 1:1 dialogs",
        },
        "explicit": explicit,
    }


def status(alias: str, config: Config | None = None) -> dict:
    conn = _open_existing(alias, config)
    try:
        meta = store_mod.require_bound_alias(conn, alias)
        counts = store_mod.counts(conn)
        transcript_status = store_mod.transcript_status_counts(conn)
        transcript_errors = store_mod.transcript_errors(conn)
        dialogs = store_mod.list_sync_state(conn)
        account_sync = store_mod.read_account_sync(conn)
        last_errors = [
            {"peer_id": row["peer_id"], "error": row["last_error"]}
            for row in dialogs
            if row.get("last_error")
        ]
        version = store_mod.schema_version(conn)
    finally:
        conn.close()
    return {
        "account": {
            "alias": meta["account_alias"],
            "user_id": meta["account_user_id"],
        },
        "path": str(db_path(alias, config)),
        "schema_version": version,
        "counts": counts,
        "dialogs": dialogs,
        "transcript_queue": counts["transcript_queue"],
        "transcript_status": transcript_status,
        "transcript_errors": transcript_errors,
        "last_errors": last_errors,
        "gap": account_sync["gap"],
        "last_sync_at": account_sync["last_sync_at"],
        "reconcile": account_sync["reconcile"],
        "has_cursor": account_sync["changes_cursor"] is not None,
    }


def search(
    alias: str,
    query: str,
    *,
    chat: str | None = None,
    from_user: str | None = None,
    since=None,
    until=None,
    kind: str | None = None,
    transcripts_only: bool = False,
    sort: str = "relevance",
    limit: int | None = None,
    page: int | None = None,
    config: Config | None = None,
) -> dict:
    return _offline(
        alias,
        explore_mod.search,
        query,
        chat=chat,
        from_user=from_user,
        since=since,
        until=until,
        kind=kind,
        transcripts_only=transcripts_only,
        sort=sort,
        limit=DEFAULT_SEARCH_LIMIT if limit is None else limit,
        page=1 if page is None else page,
        config=config,
    )


def read(
    alias: str,
    chat: str,
    *,
    around_id: int | None = None,
    around_date=None,
    since=None,
    until=None,
    limit: int | None = None,
    config: Config | None = None,
) -> dict:
    return _offline(
        alias,
        explore_mod.read,
        chat,
        around_id=around_id,
        around_date=around_date,
        since=since,
        until=until,
        limit=DEFAULT_READ_LIMIT if limit is None else limit,
        config=config,
    )


def history(
    alias: str, chat: str, message_id: int, config: Config | None = None
) -> dict:
    return _offline(alias, explore_mod.history, chat, message_id, config=config)


async def backfill(
    tg,
    alias: str,
    chats: list[str],
    *,
    limit: int | None = None,
    private: bool = False,
    max_dialogs: int | None = None,
    config: Config | None = None,
) -> dict:
    backfill_mod.validate_private_mode(private=private, chats=chats)
    limit = backfill_mod.validate_limit(
        limit, default=DEFAULT_BACKFILL_LIMIT, maximum=MAX_BACKFILL_LIMIT
    )
    me = await tg.get_me()
    conn = _open_existing(alias, config)
    try:
        store_mod.require_bound_user(conn, int(me.id), alias)
        if private:
            max_dialogs = backfill_mod.validate_max_dialogs(
                max_dialogs,
                default=DEFAULT_PRIVATE_DIALOGS,
                maximum=MAX_PRIVATE_DIALOGS,
            )
            data = await backfill_mod.backfill_private(
                tg,
                conn,
                limit=limit,
                max_dialogs=max_dialogs,
                account_user_id=int(me.id),
            )
        else:
            chats = backfill_mod.validate_dialogs(chats, maximum=MAX_BACKFILL_DIALOGS)
            dialogs, stop_reason = await backfill_mod.backfill_dialogs(
                tg,
                conn,
                chats,
                limit=limit,
                account_user_id=int(me.id),
            )
            data = {
                "mode": "chats",
                "limit": limit,
                "dialogs": dialogs,
                "stored": sum(item["stored"] for item in dialogs),
            }
            if stop_reason is not None:
                data["stop_reason"] = stop_reason
                data["deferred"] = len(chats) - len(dialogs)
                resume = chats[len(dialogs)] if len(dialogs) < len(chats) else None
                data["resume"] = resume
        data["media"] = await sync_mod.fetch_media(
            tg,
            conn,
            account_alias=alias,
            account_user_id=int(me.id),
            account_dir=account_dir(alias, config),
            limit=DEFAULT_SYNC_MEDIA,
        )
        data["remaining"] = bool(
            any(item["more"] for item in data["dialogs"])
            or data.get("deferred", 0)
            or data["media"]["remaining"]
        )
        data["account"] = {"alias": alias, "user_id": int(me.id)}
        return data
    finally:
        conn.close()


async def sync(
    tg,
    alias: str,
    *,
    max_events: int | None = None,
    max_dialogs: int | None = None,
    max_media: int | None = None,
    config: Config | None = None,
    should_stop=None,
) -> dict:
    max_events = sync_mod.validate_max_events(
        max_events, default=DEFAULT_SYNC_EVENTS, maximum=MAX_SYNC_EVENTS
    )
    max_dialogs = sync_mod.validate_max_dialogs(
        max_dialogs, default=DEFAULT_SYNC_DIALOGS, maximum=MAX_SYNC_DIALOGS
    )
    max_media = sync_mod.validate_max_media(
        max_media, default=DEFAULT_SYNC_MEDIA, maximum=MAX_SYNC_MEDIA
    )
    me = await tg.get_me()
    conn = _open_existing(alias, config)
    try:
        store_mod.require_bound_user(conn, int(me.id), alias)
        data = await sync_mod.sync_archive(
            tg,
            conn,
            account_user_id=int(me.id),
            max_events=max_events,
            max_dialogs=max_dialogs,
            max_media=max_media,
            account_alias=alias,
            account_dir=account_dir(alias, config),
            should_stop=should_stop,
        )
    finally:
        conn.close()
    data["account"] = {"alias": alias, "user_id": int(me.id)}
    data["max_events"] = max_events
    data["max_dialogs"] = max_dialogs
    data["max_media"] = max_media
    return data


async def rebaseline(tg, alias: str, *, config: Config | None = None) -> dict:
    me = await tg.get_me()
    conn = _open_existing(alias, config)
    try:
        store_mod.require_bound_user(conn, int(me.id), alias)
        data = await sync_mod.rebaseline(tg, conn)
    finally:
        conn.close()
    data["account"] = {"alias": alias, "user_id": int(me.id)}
    return data


def transcribe(
    alias: str,
    *,
    limit: int | None = None,
    max_attempts: int | None = None,
    config: Config | None = None,
) -> dict:
    limit = transcribe_mod.validate_limit(limit)
    max_attempts = transcribe_mod.validate_max_attempts(max_attempts)
    conn = _open_existing(alias, config)
    try:
        store_mod.require_bound_alias(conn, alias)
        data = transcribe_mod.run_queue(
            conn,
            account_dir(alias, config),
            limit=limit,
            max_attempts=max_attempts,
        )
    finally:
        conn.close()
    data["account"] = {"alias": alias}
    return data


def init_rows(data: dict) -> list[tuple]:
    account = data["account"]
    return [
        ("created", data["created"]),
        ("alias", account["alias"]),
        ("user_id", account["user_id"]),
        ("path", data["path"]),
    ]


def add_rows(data: dict) -> list[tuple]:
    entry = data["added"]
    return [
        ("peer_id", entry["peer_id"]),
        ("kind", entry["kind"]),
        ("title", entry.get("title")),
        ("created", entry.get("created")),
    ]


def remove_rows(data: dict) -> list[tuple]:
    entry = data["removed"]
    return [
        ("peer_id", entry["peer_id"]),
        ("kind", entry["kind"]),
        ("title", entry.get("title")),
    ]


def list_rows(data: dict) -> list[tuple]:
    rows: list[tuple] = [("standing", "private", None, data["standing"]["description"])]
    for entry in data["explicit"]:
        rows.append(
            (
                entry["kind"],
                entry["peer_id"],
                entry.get("username") or entry.get("chat_ref"),
                entry.get("title"),
            )
        )
    return rows


def status_rows(data: dict) -> list[tuple]:
    counts = data["counts"]
    gap = data.get("gap")
    return [
        ("alias", data["account"]["alias"]),
        ("user_id", data["account"]["user_id"]),
        ("messages", counts["messages"]),
        ("revisions", counts["revisions"]),
        ("tombstones", counts["tombstones"]),
        ("scope", counts["scope"]),
        ("transcript_queue", data["transcript_queue"]),
        ("transcript_no_transcript", data["transcript_status"]["no_transcript"]),
        ("transcript_errors", len(data["transcript_errors"])),
        ("dialogs", len(data["dialogs"])),
        ("last_errors", len(data["last_errors"])),
        ("gap", None if gap is None else gap.get("reason")),
        ("has_cursor", data.get("has_cursor")),
    ]


def transcribe_rows(data: dict) -> list[tuple]:
    return [
        ("queued", data["queued"]),
        ("attempted", data["attempted"]),
        ("transcribed", data["transcribed"]),
        ("retryable", data["retryable"]),
        ("no_transcript", data["no_transcript"]),
        ("skipped_missing_media", data["skipped_missing_media"]),
        ("remaining", data["remaining"]),
    ]


search_rows = explore_mod.search_rows
read_rows = explore_mod.read_rows
history_rows = explore_mod.history_rows


def backfill_rows(data: dict) -> list[tuple]:
    rows: list[tuple] = [
        ("mode", data.get("mode", "chats")),
        ("stored", data["stored"]),
        ("limit", data["limit"]),
    ]
    if data.get("mode") == "private":
        rows.append(("skipped_complete", data.get("skipped_complete", 0)))
    media = data.get("media") or {}
    rows.extend(
        [
            ("media_queued", media.get("queued", 0)),
            ("media_downloaded", media.get("downloaded", 0)),
            ("media_failed", len(media.get("failed") or [])),
        ]
    )
    for item in data["dialogs"]:
        rows.append(
            (
                item["chat"],
                item["stored"],
                item["inserted"],
                item["updated"],
                item["more"],
            )
        )
    return rows


def sync_rows(data: dict) -> list[tuple]:
    applied = data["applied"]
    gap = data.get("gap")
    media = data.get("media") or {}
    return [
        ("initialized", data.get("initialized")),
        ("events", applied["events"]),
        ("received", applied.get("received", applied["events"])),
        ("inserted", applied["inserted"]),
        ("updated", applied["updated"]),
        ("edits", applied["edits"]),
        ("tombstones", applied["tombstones"]),
        ("media_queued", media.get("queued", 0)),
        ("media_downloaded", media.get("downloaded", 0)),
        ("media_failed", len(media.get("failed") or [])),
        ("gap", None if gap is None else gap.get("reason")),
    ]


def rebaseline_rows(data: dict) -> list[tuple]:
    return [
        ("rebaselined", data["rebaselined"]),
        ("peers", len(data.get("peers") or [])),
        ("gap", data.get("gap")),
    ]


def resolve_alias(account_flag: str | None, config: Config | None = None) -> str:
    cfg = config if config is not None else load_config()
    return resolve_account(cfg, account_flag).alias


# Re-export for store stats callers that only need the default root under state_dir.
def default_state_archive_root() -> Path:
    return state_dir() / "archive"
