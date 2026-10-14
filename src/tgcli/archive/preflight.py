"""Fail-closed CLI validation for the `tg archive` subsystem.

Each public `prepare_*`/`apply_*` is one leaf's preflight hook in
`tgcli.registry`; the readonly gate is declared there too.
"""

from __future__ import annotations

from tgcli.archive import (
    backfill as backfill_mod,
    explore as explore_mod,
    limits,
    search as search_mod,
)
from tgcli.errors import PolicyError


def prepare_search(args) -> None:
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
    args.limit = limits.bounded("search", "--limit", getattr(args, "limit", None))
    args.page = limits.bounded("search", "--page", getattr(args, "page", None))


def prepare_read(args) -> None:
    if not str(args.chat).strip():
        raise PolicyError("archive read CHAT must be non-empty")
    explore_mod.validate_date_range(args.since, args.until, "read")
    explore_mod.validate_read_centers(
        getattr(args, "around_id", None), args.around_date
    )
    args.limit = limits.bounded("read", "--limit", getattr(args, "limit", None))


def prepare_history(args) -> None:
    if not str(args.chat).strip():
        raise PolicyError("archive history CHAT must be non-empty")
    args.message_id = explore_mod.validate_message_id(args.message_id)


def apply_sync(args) -> None:
    args.max_dialogs = limits.bounded(
        "sync", "--max-dialogs", getattr(args, "max_dialogs", None)
    )
    args.max_events = limits.bounded(
        "sync", "--max-events", getattr(args, "max_events", None)
    )
    args.max_media = limits.bounded(
        "sync", "--max-media", getattr(args, "max_media", None)
    )


def prepare_transcribe(args) -> None:
    args.limit = limits.bounded("transcribe", "--limit", getattr(args, "limit", None))
    args.max_attempts = limits.bounded(
        "transcribe", "--max-attempts", getattr(args, "max_attempts", None)
    )


def apply_backfill(args) -> None:
    private = bool(getattr(args, "private", False))
    chats = list(getattr(args, "chats", None) or [])
    backfill_mod.validate_private_mode(private=private, chats=chats)
    if not private:
        chats = backfill_mod.validate_dialogs(chats, maximum=limits.BACKFILL_MAX_CHATS)
    args.chats = chats
    args.limit = limits.bounded("backfill", "--limit", getattr(args, "limit", None))
    args.private = private
    if private:
        args.max_dialogs = limits.bounded(
            "backfill", "--max-dialogs", getattr(args, "max_dialogs", None)
        )
