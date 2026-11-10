"""Every `tg archive` leaf's handler, offline and network, for `tgcli.registry`.

Offline leaves take ``(args, config)``; network leaves take ``(tg, args,
account)`` inside the session dispatch opens. Each returns ``(data, rows)``.
"""

from __future__ import annotations

from tgcli.archive import explore as explore_mod
from tgcli.commands import archive as archive_cmd
from tgcli.config import Config
from tgcli.governor import pacing


def list_scope(args, config: Config) -> tuple[dict, list[tuple]]:
    alias = archive_cmd.resolve_alias(args.account, config)
    data = archive_cmd.list_scope(alias, config)
    return data, archive_cmd.list_rows(data)


def status(args, config: Config) -> tuple[dict, list[tuple]]:
    alias = archive_cmd.resolve_alias(args.account, config)
    data = archive_cmd.status(alias, config)
    return data, archive_cmd.status_rows(data)


def transcribe(args, config: Config) -> tuple[dict, list[tuple]]:
    alias = archive_cmd.resolve_alias(args.account, config)
    data = archive_cmd.transcribe(
        alias,
        limit=args.limit,
        max_attempts=args.max_attempts,
        config=config,
        should_stop=pacing.wall_clock_exhausted,
    )
    return data, archive_cmd.transcribe_rows(data)


def read(args, config: Config) -> tuple[dict, list[tuple]]:
    alias = archive_cmd.resolve_alias(args.account, config)
    data = archive_cmd.offline_read(
        alias,
        explore_mod.read,
        args.chat,
        around_id=getattr(args, "around_id", None),
        around_date=getattr(args, "around_date", None),
        since=getattr(args, "since", None),
        until=getattr(args, "until", None),
        limit=args.limit,
        config=config,
    )
    return data, explore_mod.read_rows(data)


def history(args, config: Config) -> tuple[dict, list[tuple]]:
    alias = archive_cmd.resolve_alias(args.account, config)
    data = archive_cmd.offline_read(
        alias, explore_mod.history, args.chat, args.message_id, config=config
    )
    return data, explore_mod.history_rows(data)


def search(args, config: Config) -> tuple[dict, list[tuple]]:
    alias = archive_cmd.resolve_alias(args.account, config)
    data = archive_cmd.offline_read(
        alias,
        explore_mod.search,
        args.query,
        chat=getattr(args, "chat", None),
        from_user=getattr(args, "from_user", None),
        since=getattr(args, "since", None),
        until=getattr(args, "until", None),
        kind=getattr(args, "kind", None),
        transcripts_only=bool(getattr(args, "transcripts_only", False)),
        sort=getattr(args, "sort", "relevance"),
        limit=args.limit,
        page=args.page,
        config=config,
    )
    return data, explore_mod.search_rows(data)


async def init(tg, args, account) -> tuple[dict, list[tuple]]:
    data = await archive_cmd.init_archive(tg, account.alias)
    return data, archive_cmd.init_rows(data)


async def add(tg, args, account) -> tuple[dict, list[tuple]]:
    data = await archive_cmd.add_chat(tg, account.alias, args.chat)
    return data, archive_cmd.add_rows(data)


async def remove(tg, args, account) -> tuple[dict, list[tuple]]:
    data = await archive_cmd.remove_chat(tg, account.alias, args.chat)
    return data, archive_cmd.remove_rows(data)


async def backfill(tg, args, account) -> tuple[dict, list[tuple]]:
    data = await archive_cmd.backfill(
        tg,
        account.alias,
        list(getattr(args, "chats", None) or []),
        limit=args.limit,
        private=bool(getattr(args, "private", False)),
        max_dialogs=args.max_dialogs,
        should_stop=pacing.wall_clock_exhausted,
    )
    return data, archive_cmd.backfill_rows(data)


async def sync(tg, args, account) -> tuple[dict, list[tuple]]:
    data = await archive_cmd.sync(
        tg,
        account.alias,
        max_events=args.max_events,
        max_dialogs=args.max_dialogs,
        max_media=args.max_media,
        should_stop=pacing.wall_clock_exhausted,
    )
    if data.get("remaining") and pacing.wall_clock_exhausted():
        data["stop_reason"] = "wall_clock_cap"
    return data, archive_cmd.sync_rows(data)


async def rebaseline(tg, args, account) -> tuple[dict, list[tuple]]:
    data = await archive_cmd.rebaseline(tg, account.alias)
    return data, archive_cmd.rebaseline_rows(data)
