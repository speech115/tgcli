"""Fail-closed CLI validation for the `tg archive` subsystem."""

from __future__ import annotations

from tgcli import safety
from tgcli.archive import (
    backfill as backfill_mod,
    explore as explore_mod,
    search as search_mod,
    sync as sync_mod,
    transcribe as transcribe_mod,
)
from tgcli.commands import archive as archive_cmd
from tgcli.errors import PolicyError

_LOCAL_MUTATION_COMMANDS = frozenset(
    {"init", "add", "remove", "backfill", "sync", "rebaseline", "transcribe"}
)


def uses_time_bounds(args) -> bool:
    return args.command == "archive" and args.archive_command in ("read", "search")


def backfill_spec(*, chats, private: bool, limit) -> dict:
    """Normalized archive-backfill payload shared by CLI and jobs."""
    chat_list = list(chats)
    backfill_mod.validate_private_mode(private=private, chats=chat_list)
    if not private:
        chat_list = backfill_mod.validate_dialogs(
            chat_list, maximum=archive_cmd.MAX_BACKFILL_DIALOGS
        )
    return {
        "chats": chat_list,
        "limit": backfill_mod.validate_limit(
            limit,
            default=archive_cmd.DEFAULT_BACKFILL_LIMIT,
            maximum=archive_cmd.MAX_BACKFILL_LIMIT,
        ),
        "private": private,
    }


def sync_spec(*, max_events, max_dialogs, max_media) -> dict:
    """Normalized archive-sync payload shared by CLI and jobs."""
    return {
        "max_dialogs": sync_mod.validate_max_dialogs(
            max_dialogs,
            default=archive_cmd.DEFAULT_SYNC_DIALOGS,
            maximum=archive_cmd.MAX_SYNC_DIALOGS,
        ),
        "max_events": sync_mod.validate_max_events(
            max_events,
            default=archive_cmd.DEFAULT_SYNC_EVENTS,
            maximum=archive_cmd.MAX_SYNC_EVENTS,
        ),
        "max_media": sync_mod.validate_max_media(
            max_media,
            default=archive_cmd.DEFAULT_SYNC_MEDIA,
            maximum=archive_cmd.MAX_SYNC_MEDIA,
        ),
    }


def prepare(args) -> None:
    if args.command != "archive":
        return
    cmd = args.archive_command
    if cmd in _LOCAL_MUTATION_COMMANDS:
        safety.enforce_local_mutation_allowed(args.readonly)
    if cmd == "search":
        _prepare_search(args)
        return
    if cmd == "read":
        _prepare_read(args)
        return
    if cmd == "history":
        _prepare_history(args)
        return
    if cmd == "sync":
        _apply_sync(args)
        return
    if cmd == "transcribe":
        _prepare_transcribe(args)
        return
    if cmd != "backfill":
        return
    _apply_backfill(args)


def _prepare_search(args) -> None:
    args.query = search_mod.validate_query(getattr(args, "query", None))
    chat = getattr(args, "chat", None)
    if chat is not None and not str(chat).strip():
        raise PolicyError("archive search --chat must be non-empty")
    args.from_user = explore_mod.validate_filter(
        getattr(args, "from_user", None), "--from"
    )
    args.kind = explore_mod.validate_kind(getattr(args, "kind", None))
    args.sort = explore_mod.validate_sort(getattr(args, "sort", None))
    explore_mod.validate_date_range(args.since, args.until, "search")
    args.limit = explore_mod.validate_limit(
        getattr(args, "limit", None),
        default=archive_cmd.DEFAULT_SEARCH_LIMIT,
        maximum=archive_cmd.MAX_SEARCH_LIMIT,
        label="search",
    )
    args.page = explore_mod.validate_page(getattr(args, "page", None))


def _prepare_read(args) -> None:
    if not str(args.chat).strip():
        raise PolicyError("archive read CHAT must be non-empty")
    explore_mod.validate_date_range(args.since, args.until, "read")
    explore_mod.validate_read_centers(
        getattr(args, "around_id", None), args.around_date
    )
    args.limit = explore_mod.validate_limit(
        getattr(args, "limit", None),
        default=archive_cmd.DEFAULT_READ_LIMIT,
        maximum=archive_cmd.MAX_READ_LIMIT,
        label="read",
    )


def _prepare_history(args) -> None:
    if not str(args.chat).strip():
        raise PolicyError("archive history CHAT must be non-empty")
    args.message_id = explore_mod.validate_message_id(args.message_id)


def _apply_sync(args) -> None:
    spec = sync_spec(
        max_events=getattr(args, "max_events", None),
        max_dialogs=getattr(args, "max_dialogs", None),
        max_media=getattr(args, "max_media", None),
    )
    args.max_events = spec["max_events"]
    args.max_dialogs = spec["max_dialogs"]
    args.max_media = spec["max_media"]


def _prepare_transcribe(args) -> None:
    args.limit = transcribe_mod.validate_limit(getattr(args, "limit", None))
    args.max_attempts = transcribe_mod.validate_max_attempts(
        getattr(args, "max_attempts", None)
    )


def _apply_backfill(args) -> None:
    private = bool(getattr(args, "private", False))
    spec = backfill_spec(
        chats=list(getattr(args, "chats", None) or []),
        private=private,
        limit=getattr(args, "limit", None),
    )
    args.chats = spec["chats"]
    args.limit = spec["limit"]
    args.private = spec["private"]
    if private:
        args.max_dialogs = backfill_mod.validate_max_dialogs(
            getattr(args, "max_dialogs", None),
            default=archive_cmd.DEFAULT_PRIVATE_DIALOGS,
            maximum=archive_cmd.MAX_PRIVATE_DIALOGS,
        )
