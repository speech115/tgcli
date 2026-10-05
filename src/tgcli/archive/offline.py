"""Offline archive commands that need no Telegram session."""

from __future__ import annotations

from tgcli.commands import archive as archive_cmd
from tgcli.config import Config
from tgcli.governor import pacing

_OFFLINE_COMMANDS = frozenset(
    {"list", "status", "search", "read", "history", "transcribe"}
)


def execute(args, config: Config) -> tuple[dict, list[tuple]] | None:
    """Run local-only archive commands; network commands return None."""
    if args.command != "archive":
        return None
    cmd = args.archive_command
    if cmd not in _OFFLINE_COMMANDS:
        return None
    alias = archive_cmd.resolve_alias(args.account, config)
    if cmd == "list":
        data = archive_cmd.list_scope(alias, config)
        return data, archive_cmd.list_rows(data)
    if cmd == "status":
        data = archive_cmd.status(alias, config)
        return data, archive_cmd.status_rows(data)
    if cmd == "transcribe":
        data = archive_cmd.transcribe(
            alias,
            limit=getattr(args, "limit", None),
            max_attempts=getattr(args, "max_attempts", None),
            config=config,
            should_stop=pacing.wall_clock_exhausted,
        )
        return data, archive_cmd.transcribe_rows(data)
    if cmd == "read":
        data = archive_cmd.read(
            alias,
            args.chat,
            around_id=getattr(args, "around_id", None),
            around_date=getattr(args, "around_date", None),
            since=getattr(args, "since", None),
            until=getattr(args, "until", None),
            limit=getattr(args, "limit", None),
            config=config,
        )
        return data, archive_cmd.read_rows(data)
    if cmd == "history":
        data = archive_cmd.history(alias, args.chat, args.message_id, config=config)
        return data, archive_cmd.history_rows(data)
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
        limit=getattr(args, "limit", None),
        page=getattr(args, "page", None),
        config=config,
    )
    return data, archive_cmd.search_rows(data)
