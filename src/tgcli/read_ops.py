"""Typed read operations shared by the interactive and batch adapters."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar, Literal, NoReturn, TypeAlias

from tgcli.commands import (
    dialogs as dialogs_cmd,
    draft as draft_cmd,
    identity as identity_cmd,
    info as info_cmd,
    media as media_cmd,
    read as read_cmd,
    search as search_cmd,
    thread as thread_cmd,
)


@dataclass(frozen=True)
class Dialogs:
    limit: int
    unread_only: bool
    kind: str | None
    name: ClassVar[Literal["dialogs"]] = "dialogs"


@dataclass(frozen=True)
class Read:
    chat: str
    limit: int
    before_id: int | None
    after_id: int | None
    since: datetime | None
    until: datetime | None
    topic: int | None
    name: ClassVar[Literal["read"]] = "read"


@dataclass(frozen=True)
class Search:
    chat: str | None
    query: str
    limit: int
    all: bool
    from_user: str | None
    since: datetime | None
    name: ClassVar[Literal["search"]] = "search"


@dataclass(frozen=True)
class Latest:
    chat: str
    name: ClassVar[Literal["latest"]] = "latest"


@dataclass(frozen=True)
class Message:
    chat: str
    message_id: int
    context: int
    name: ClassVar[Literal["message"]] = "message"


@dataclass(frozen=True)
class Info:
    chat: str
    full: bool
    name: ClassVar[Literal["info"]] = "info"


@dataclass(frozen=True)
class Count:
    chat: str
    name: ClassVar[Literal["count"]] = "count"


@dataclass(frozen=True)
class Resolve:
    ref: str
    name: ClassVar[Literal["resolve"]] = "resolve"


@dataclass(frozen=True)
class ContactsList:
    name: ClassVar[Literal["contacts.list"]] = "contacts.list"


@dataclass(frozen=True)
class ContactsSearch:
    query: str
    use_global: bool
    name: ClassVar[Literal["contacts.search"]] = "contacts.search"


@dataclass(frozen=True)
class MediaManifest:
    source: str
    kind: str | None
    since: datetime | None
    limit: int
    name: ClassVar[Literal["media.manifest"]] = "media.manifest"


@dataclass(frozen=True)
class Thread:
    chat: str
    message_id: int
    depth: int
    want_replies: bool
    replies_limit: int
    name: ClassVar[Literal["thread"]] = "thread"


@dataclass(frozen=True)
class Draft:
    chat: str
    name: ClassVar[Literal["draft.show"]] = "draft.show"


@dataclass(frozen=True)
class Drafts:
    name: ClassVar[Literal["draft.list"]] = "draft.list"


ReadOperation: TypeAlias = (
    Dialogs
    | Read
    | Search
    | Latest
    | Message
    | Info
    | Count
    | Resolve
    | ContactsList
    | ContactsSearch
    | MediaManifest
    | Thread
    | Draft
    | Drafts
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


async def _fetch_search(tg, op: Search) -> dict[str, Any]:
    if op.all:
        return await search_cmd.fetch_search_all(tg, op.query, limit=op.limit)
    if op.chat is None:
        raise AssertionError("scoped search requires a chat")
    return await search_cmd.fetch_search(
        tg,
        op.chat,
        op.query,
        limit=op.limit,
        from_user=op.from_user,
        since=op.since,
    )


async def _fetch_info(tg, op: Info) -> dict[str, Any]:
    if op.full:
        return await info_cmd.fetch_info_full(tg, op.chat)
    return await info_cmd.fetch_info(tg, op.chat)


@dataclass(frozen=True)
class _Spec:
    """One operation's three adapters, kept adjacent so they cannot drift apart.

    `cli` builds the operation from an argparse namespace, `fetch` runs it,
    `rows` renders the `--plain` projection of what it returned. Adding a read
    operation means adding one row here; the CLI allowlist and dispatch both
    derive from this table.
    """

    cli: Callable[[Any], ReadOperation]
    fetch: Callable[[Any, Any], Awaitable[dict[str, Any]]]
    rows: Callable[[dict[str, Any]], list[tuple]]


_SPECS: dict[str, _Spec] = {
    "dialogs": _Spec(
        cli=lambda a: Dialogs(a.limit, a.unread_only, a.kind),
        fetch=lambda tg, op: dialogs_cmd.fetch_dialogs(
            tg, limit=op.limit, unread_only=op.unread_only, kind=op.kind
        ),
        rows=dialogs_cmd.to_rows,
    ),
    "read": _Spec(
        cli=lambda a: Read(
            a.chat, a.limit, a.before_id, a.after_id, a.since, a.until, a.topic
        ),
        fetch=lambda tg, op: read_cmd.fetch_messages(
            tg,
            op.chat,
            limit=op.limit,
            before_id=op.before_id,
            after_id=op.after_id,
            since=op.since,
            until=op.until,
            topic=op.topic,
        ),
        rows=read_cmd.to_rows,
    ),
    "search": _Spec(
        cli=lambda a: Search(a.chat, a.query, a.limit, a.all, a.from_user, a.since),
        fetch=_fetch_search,
        rows=search_cmd.to_rows,
    ),
    "latest": _Spec(
        cli=lambda a: Latest(a.chat),
        fetch=lambda tg, op: search_cmd.fetch_latest(tg, op.chat),
        rows=search_cmd.to_rows,
    ),
    "message": _Spec(
        cli=lambda a: Message(a.chat, a.message_id, a.context),
        fetch=lambda tg, op: read_cmd.fetch_message(
            tg, op.chat, op.message_id, context=op.context
        ),
        rows=search_cmd.to_rows,
    ),
    "info": _Spec(
        cli=lambda a: Info(a.chat, a.full),
        fetch=_fetch_info,
        rows=info_cmd.to_rows,
    ),
    "count": _Spec(
        cli=lambda a: Count(a.chat),
        fetch=lambda tg, op: info_cmd.fetch_count(tg, op.chat),
        rows=info_cmd.to_rows,
    ),
    "resolve": _Spec(
        cli=lambda a: Resolve(a.ref),
        fetch=lambda tg, op: identity_cmd.resolve(tg, op.ref),
        rows=identity_cmd.to_rows,
    ),
    "contacts.list": _Spec(
        cli=lambda a: ContactsList(),
        fetch=lambda tg, op: identity_cmd.contacts_list(tg),
        rows=identity_cmd.contacts_to_rows,
    ),
    "contacts.search": _Spec(
        cli=lambda a: ContactsSearch(a.query, a.use_global),
        fetch=lambda tg, op: identity_cmd.contacts_search(
            tg, op.query, use_global=op.use_global
        ),
        rows=identity_cmd.contacts_to_rows,
    ),
    "media.manifest": _Spec(
        cli=lambda a: MediaManifest(a.source, a.media_type, a.since, a.limit),
        fetch=lambda tg, op: media_cmd.manifest(
            tg, op.source, kind=op.kind, since=op.since, limit=op.limit
        ),
        rows=media_cmd.manifest_to_rows,
    ),
    "thread": _Spec(
        cli=lambda a: Thread(a.chat, a.message_id, a.depth, a.replies, a.limit),
        fetch=lambda tg, op: thread_cmd.fetch_thread(
            tg,
            op.chat,
            op.message_id,
            depth=op.depth,
            want_replies=op.want_replies,
            replies_limit=op.replies_limit,
        ),
        rows=thread_cmd.to_rows,
    ),
    "draft.show": _Spec(
        cli=lambda a: Draft(a.chat),
        fetch=lambda tg, op: draft_cmd.fetch_show(tg, op.chat),
        rows=draft_cmd.show_to_rows,
    ),
    "draft.list": _Spec(
        cli=lambda a: Drafts(),
        fetch=lambda tg, op: draft_cmd.fetch_list(tg),
        rows=draft_cmd.list_to_rows,
    ),
}


def _cli_op_name(args) -> str | None:
    """The op a CLI invocation maps to, or None if it is not a read command.

    Two commands fan out to more than one op; every other read command's name
    is its op name.
    """
    if args.command == "contacts":
        return "contacts.list" if args.contacts_command == "list" else "contacts.search"
    if args.command == "media":
        return "media.manifest" if args.media_command == "manifest" else None
    if args.command == "draft":
        if args.draft_command == "show":
            return "draft.show"
        if args.draft_command == "list":
            return "draft.list"
        return None
    return args.command if args.command in _SPECS else None


def from_cli(args) -> ReadOperation | None:
    name = _cli_op_name(args)
    return None if name is None else _SPECS[name].cli(args)


async def execute(tg, operation: ReadOperation) -> Result:
    spec = _SPECS.get(operation.name)
    if spec is None:
        raise AssertionError(f"unhandled read operation: {operation!r}")
    data = await spec.fetch(tg, operation)
    return Result(data, spec.rows(data))
