"""Typed read operations shared by the interactive and batch adapters."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Callable, NoReturn, TypeAlias

from tgcli.commands import dialogs as dialogs_cmd
from tgcli.commands import identity as identity_cmd
from tgcli.commands import info as info_cmd
from tgcli.commands import media as media_cmd
from tgcli.commands import read as read_cmd
from tgcli.commands import search as search_cmd
from tgcli.commands import thread as thread_cmd
from tgcli.errors import PolicyError


@dataclass(frozen=True)
class Dialogs:
    limit: int
    unread_only: bool
    kind: str | None
    name: str = "dialogs"


@dataclass(frozen=True)
class Read:
    chat: str
    limit: int
    before_id: int | None
    after_id: int | None
    since: datetime | None
    until: datetime | None
    topic: int | None
    name: str = "read"


@dataclass(frozen=True)
class Search:
    chat: str | None
    query: str
    limit: int
    all: bool
    from_user: str | None
    since: datetime | None
    name: str = "search"


@dataclass(frozen=True)
class Latest:
    chat: str
    name: str = "latest"


@dataclass(frozen=True)
class Message:
    chat: str
    message_id: int
    context: int
    name: str = "message"


@dataclass(frozen=True)
class Info:
    chat: str
    full: bool
    name: str = "info"


@dataclass(frozen=True)
class Count:
    chat: str
    name: str = "count"


@dataclass(frozen=True)
class Resolve:
    ref: str
    name: str = "resolve"


@dataclass(frozen=True)
class MutualChats:
    ref: str
    name: str = "mutual-chats"


@dataclass(frozen=True)
class ContactsList:
    name: str = "contacts.list"


@dataclass(frozen=True)
class ContactsSearch:
    query: str
    use_global: bool
    name: str = "contacts.search"


@dataclass(frozen=True)
class MediaManifest:
    source: str
    kind: str | None
    since: datetime | None
    limit: int
    name: str = "media.manifest"


@dataclass(frozen=True)
class Thread:
    chat: str
    message_id: int
    depth: int
    want_replies: bool
    replies_limit: int
    name: str = "thread"


ReadOperation: TypeAlias = (
    Dialogs
    | Read
    | Search
    | Latest
    | Message
    | Info
    | Count
    | Resolve
    | MutualChats
    | ContactsList
    | ContactsSearch
    | MediaManifest
    | Thread
)

BATCH_OP_NAMES = frozenset(
    {
        "dialogs",
        "read",
        "search",
        "latest",
        "message",
        "info",
        "count",
        "resolve",
        "mutual-chats",
        "contacts.list",
        "contacts.search",
        "media.manifest",
        "thread",
    }
)


@dataclass(frozen=True)
class Result:
    data: dict[str, Any]
    rows: list[tuple]


def parse_when(
    value: Any,
    *,
    invalid: Callable[[], NoReturn],
) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        parsed = value
    else:
        if not isinstance(value, str):
            invalid()
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            invalid()
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _batch_when(value: Any, field: str) -> datetime | None:
    def invalid() -> NoReturn:
        raise PolicyError(f"batch {field} must be an ISO 8601 string")

    return parse_when(value, invalid=invalid)


def from_cli(args) -> ReadOperation | None:
    if args.command == "dialogs":
        return Dialogs(args.limit, args.unread_only, args.kind)
    if args.command == "read":
        return Read(
            args.chat,
            args.limit,
            args.before_id,
            args.after_id,
            args.since,
            args.until,
            args.topic,
        )
    if args.command == "search":
        return Search(
            args.chat,
            args.query,
            args.limit,
            args.all,
            args.from_user,
            args.since,
        )
    if args.command == "latest":
        return Latest(args.chat)
    if args.command == "message":
        return Message(args.chat, args.message_id, args.context)
    if args.command == "info":
        return Info(args.chat, args.full)
    if args.command == "count":
        return Count(args.chat)
    if args.command == "resolve":
        return Resolve(args.ref)
    if args.command == "mutual-chats":
        return MutualChats(args.ref)
    if args.command == "contacts":
        if args.contacts_command == "list":
            return ContactsList()
        return ContactsSearch(args.query, args.use_global)
    if args.command == "media" and args.media_command == "manifest":
        return MediaManifest(
            args.source,
            args.media_type,
            args.since,
            args.limit,
        )
    if args.command == "thread":
        return Thread(
            args.chat,
            args.message_id,
            args.depth,
            args.replies,
            args.limit,
        )
    return None


def from_batch(payload: dict[str, Any]) -> ReadOperation:
    op = payload["op"]
    if op == "dialogs":
        return Dialogs(
            int(payload.get("limit", 50)),
            bool(payload.get("unread_only", False)),
            payload.get("kind"),
        )
    if op == "read":
        return Read(
            payload["chat"],
            int(payload.get("limit", 20)),
            payload.get("before_id"),
            payload.get("after_id"),
            _batch_when(payload.get("since"), "read.since"),
            _batch_when(payload.get("until"), "read.until"),
            payload.get("topic"),
        )
    if op == "search":
        return Search(
            None if payload.get("all") else payload["chat"],
            payload["query"],
            int(payload.get("limit", 20)),
            bool(payload.get("all")),
            None if payload.get("all") else payload.get("from"),
            (
                None
                if payload.get("all")
                else _batch_when(payload.get("since"), "search.since")
            ),
        )
    if op == "latest":
        return Latest(payload["chat"])
    if op == "message":
        return Message(
            payload["chat"],
            int(payload["message_id"]),
            int(payload.get("context", 0)),
        )
    if op == "info":
        return Info(payload["chat"], bool(payload.get("full")))
    if op == "count":
        return Count(payload["chat"])
    if op == "resolve":
        return Resolve(payload["ref"])
    if op == "mutual-chats":
        return MutualChats(payload["ref"])
    if op == "contacts.list":
        return ContactsList()
    if op == "contacts.search":
        return ContactsSearch(
            payload["query"],
            bool(payload.get("global", False)),
        )
    if op == "media.manifest":
        return MediaManifest(
            payload["source"],
            payload.get("type"),
            _batch_when(payload.get("since"), "media.manifest.since"),
            int(payload.get("limit", 100)),
        )
    if op == "thread":
        return Thread(
            payload["chat"],
            int(payload["message_id"]),
            int(payload.get("depth", 20)),
            bool(payload.get("replies", False)),
            int(payload.get("limit", 50)),
        )
    raise PolicyError(f"unhandled batch op: {op!r}")


async def execute(tg, operation: ReadOperation) -> Result:
    if isinstance(operation, Dialogs):
        data = await dialogs_cmd.fetch_dialogs(
            tg,
            limit=operation.limit,
            unread_only=operation.unread_only,
            kind=operation.kind,
        )
        return Result(data, dialogs_cmd.to_rows(data))
    if isinstance(operation, Read):
        data = await read_cmd.fetch_messages(
            tg,
            operation.chat,
            limit=operation.limit,
            before_id=operation.before_id,
            after_id=operation.after_id,
            since=operation.since,
            until=operation.until,
            topic=operation.topic,
        )
        return Result(data, read_cmd.to_rows(data))
    if isinstance(operation, Search):
        if operation.all:
            data = await search_cmd.fetch_search_all(
                tg,
                operation.query,
                limit=operation.limit,
            )
        else:
            if operation.chat is None:
                raise AssertionError("scoped search requires a chat")
            data = await search_cmd.fetch_search(
                tg,
                operation.chat,
                operation.query,
                limit=operation.limit,
                from_user=operation.from_user,
                since=operation.since,
            )
        return Result(data, search_cmd.to_rows(data))
    if isinstance(operation, Latest):
        data = await search_cmd.fetch_latest(tg, operation.chat)
        return Result(data, search_cmd.to_rows(data))
    if isinstance(operation, Message):
        data = await read_cmd.fetch_message(
            tg,
            operation.chat,
            operation.message_id,
            context=operation.context,
        )
        return Result(data, search_cmd.to_rows(data))
    if isinstance(operation, Info):
        data = (
            await info_cmd.fetch_info_full(tg, operation.chat)
            if operation.full
            else await info_cmd.fetch_info(tg, operation.chat)
        )
        return Result(data, info_cmd.to_rows(data))
    if isinstance(operation, Count):
        data = await info_cmd.fetch_count(tg, operation.chat)
        return Result(data, info_cmd.to_rows(data))
    if isinstance(operation, Resolve):
        data = await identity_cmd.resolve(tg, operation.ref)
        return Result(data, identity_cmd.to_rows(data))
    if isinstance(operation, MutualChats):
        data = await identity_cmd.mutual_chats(tg, operation.ref)
        return Result(data, identity_cmd.mutual_chats_to_rows(data))
    if isinstance(operation, ContactsList):
        data = await identity_cmd.contacts_list(tg)
        return Result(data, identity_cmd.contacts_to_rows(data))
    if isinstance(operation, ContactsSearch):
        data = await identity_cmd.contacts_search(
            tg,
            operation.query,
            use_global=operation.use_global,
        )
        return Result(data, identity_cmd.contacts_to_rows(data))
    if isinstance(operation, MediaManifest):
        data = await media_cmd.manifest(
            tg,
            operation.source,
            kind=operation.kind,
            since=operation.since,
            limit=operation.limit,
        )
        return Result(data, media_cmd.manifest_to_rows(data))
    if isinstance(operation, Thread):
        data = await thread_cmd.fetch_thread(
            tg,
            operation.chat,
            operation.message_id,
            depth=operation.depth,
            want_replies=operation.want_replies,
            replies_limit=operation.replies_limit,
        )
        return Result(data, thread_cmd.to_rows(data))
    raise AssertionError(f"unhandled read operation: {operation!r}")
