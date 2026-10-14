"""Offline archive commands that need no Telegram session.

Each function is one `tg archive` leaf's handler in `tgcli.registry`.
"""

from __future__ import annotations

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
    data = archive_cmd.read(
        alias,
        args.chat,
        around_id=getattr(args, "around_id", None),
        around_date=getattr(args, "around_date", None),
        since=getattr(args, "since", None),
        until=getattr(args, "until", None),
        limit=args.limit,
        config=config,
    )
    return data, archive_cmd.read_rows(data)


def history(args, config: Config) -> tuple[dict, list[tuple]]:
    alias = archive_cmd.resolve_alias(args.account, config)
    data = archive_cmd.history(alias, args.chat, args.message_id, config=config)
    return data, archive_cmd.history_rows(data)


def search(args, config: Config) -> tuple[dict, list[tuple]]:
    alias = archive_cmd.resolve_alias(args.account, config)
    data = archive_cmd.search(
        alias,
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
    return data, archive_cmd.search_rows(data)
