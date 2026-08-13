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
from tgcli.errors import PolicyError


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
class MutualChats:
    ref: str
    name: ClassVar[Literal["mutual-chats"]] = "mutual-chats"


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
    | MutualChats
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


def _batch_when(value: Any, field: str) -> datetime | None:
    def invalid() -> NoReturn:
        raise PolicyError(f"batch {field} must be an ISO 8601 string")

    return parse_when(value, invalid=invalid)


# Enumerations the CLI parser offers as `choices`; the batch adapter rejects
# exactly the same values, so a typo cannot come back as an empty list.
DIALOG_KINDS = ("user", "group", "channel")
MEDIA_KINDS = media_cmd.MEDIA_KINDS


def _batch_choice(value: Any, field: str, choices: tuple[str, ...]) -> str | None:
    if value is not None and value not in choices:
        raise PolicyError(f"batch {field} must be one of: {', '.join(choices)}")
    return value


def _batch_bool(value: Any, field: str) -> bool:
    """A batch bool field is a real JSON boolean; nothing else is truthy.

    Every batch bool flag defaults to `False` when absent, so a missing key
    or an explicit JSON `null` both mean "not set". Truthiness on a string
    like `"false"` or an int like `1` is exactly the T15 bug: it silently
    flips a filter instead of failing closed.
    """
    if value is None:
        return False
    if isinstance(value, bool):
        return value
    raise PolicyError(f"batch {field} must be a JSON boolean")


def _batch_int(value: Any, field: str) -> int:
    """A batch int field is a real JSON integer, matching CLI argparse
    `type=int` semantics. `bool` is excluded even though it is an `int`
    subclass in Python — `True` must not silently become `1`.
    """
    if isinstance(value, bool) or not isinstance(value, int):
        raise PolicyError(f"batch {field} must be a JSON integer")
    return value


def _batch_optional_int(value: Any, field: str) -> int | None:
    """Like `_batch_int`, but a missing key or JSON `null` means "unset"."""
    if value is None:
        return None
    return _batch_int(value, field)


def _batch_search(p: dict[str, Any]) -> Search:
    """A global search carries no chat, sender, or date scope (CONTRACT §3)."""
    if _batch_bool(p.get("all"), "search.all"):
        if p.get("from") is not None or p.get("since") is not None:
            raise PolicyError("search --all only supports QUERY and --limit")
        limit = _batch_int(p.get("limit", 20), "search.limit")
        return Search(None, p["query"], limit, True, None, None)
    return Search(
        p["chat"],
        p["query"],
        _batch_int(p.get("limit", 20), "search.limit"),
        False,
        p.get("from"),
        _batch_when(p.get("since"), "search.since"),
    )


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
    """One operation's four adapters, kept adjacent so they cannot drift apart.

    `cli` builds the operation from an argparse namespace, `batch` from a JSONL
    payload, `fetch` runs it, `rows` renders the `--plain` projection of what it
    returned. Adding a read operation means adding one row here; the CLI
    allowlist, the batch allowlist, and dispatch all derive from this table.
    """

    cli: Callable[[Any], ReadOperation]
    batch: Callable[[dict[str, Any]], ReadOperation]
    fetch: Callable[[Any, Any], Awaitable[dict[str, Any]]]
    rows: Callable[[dict[str, Any]], list[tuple]]


_SPECS: dict[str, _Spec] = {
    "dialogs": _Spec(
        cli=lambda a: Dialogs(a.limit, a.unread_only, a.kind),
        batch=lambda p: Dialogs(
            _batch_int(p.get("limit", 50), "dialogs.limit"),
            _batch_bool(p.get("unread_only"), "dialogs.unread_only"),
            _batch_choice(p.get("kind"), "dialogs.kind", DIALOG_KINDS),
        ),
        fetch=lambda tg, op: dialogs_cmd.fetch_dialogs(
            tg, limit=op.limit, unread_only=op.unread_only, kind=op.kind
        ),
        rows=dialogs_cmd.to_rows,
    ),
    "read": _Spec(
        cli=lambda a: Read(
            a.chat, a.limit, a.before_id, a.after_id, a.since, a.until, a.topic
        ),
        batch=lambda p: Read(
            p["chat"],
            _batch_int(p.get("limit", 20), "read.limit"),
            _batch_optional_int(p.get("before_id"), "read.before_id"),
            _batch_optional_int(p.get("after_id"), "read.after_id"),
            _batch_when(p.get("since"), "read.since"),
            _batch_when(p.get("until"), "read.until"),
            _batch_optional_int(p.get("topic"), "read.topic"),
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
        batch=_batch_search,
        fetch=_fetch_search,
        rows=search_cmd.to_rows,
    ),
    "latest": _Spec(
        cli=lambda a: Latest(a.chat),
        batch=lambda p: Latest(p["chat"]),
        fetch=lambda tg, op: search_cmd.fetch_latest(tg, op.chat),
        rows=search_cmd.to_rows,
    ),
    "message": _Spec(
        cli=lambda a: Message(a.chat, a.message_id, a.context),
        batch=lambda p: Message(
            p["chat"],
            _batch_int(p["message_id"], "message.message_id"),
            _batch_int(p.get("context", 0), "message.context"),
        ),
        fetch=lambda tg, op: read_cmd.fetch_message(
            tg, op.chat, op.message_id, context=op.context
        ),
        rows=search_cmd.to_rows,
    ),
    "info": _Spec(
        cli=lambda a: Info(a.chat, a.full),
        batch=lambda p: Info(p["chat"], _batch_bool(p.get("full"), "info.full")),
        fetch=_fetch_info,
        rows=info_cmd.to_rows,
    ),
    "count": _Spec(
        cli=lambda a: Count(a.chat),
        batch=lambda p: Count(p["chat"]),
        fetch=lambda tg, op: info_cmd.fetch_count(tg, op.chat),
        rows=info_cmd.to_rows,
    ),
    "resolve": _Spec(
        cli=lambda a: Resolve(a.ref),
        batch=lambda p: Resolve(p["ref"]),
        fetch=lambda tg, op: identity_cmd.resolve(tg, op.ref),
        rows=identity_cmd.to_rows,
    ),
    "mutual-chats": _Spec(
        cli=lambda a: MutualChats(a.ref),
        batch=lambda p: MutualChats(p["ref"]),
        fetch=lambda tg, op: identity_cmd.mutual_chats(tg, op.ref),
        rows=identity_cmd.mutual_chats_to_rows,
    ),
    "contacts.list": _Spec(
        cli=lambda a: ContactsList(),
        batch=lambda p: ContactsList(),
        fetch=lambda tg, op: identity_cmd.contacts_list(tg),
        rows=identity_cmd.contacts_to_rows,
    ),
    "contacts.search": _Spec(
        cli=lambda a: ContactsSearch(a.query, a.use_global),
        batch=lambda p: ContactsSearch(
            p["query"], _batch_bool(p.get("global"), "contacts.search.global")
        ),
        fetch=lambda tg, op: identity_cmd.contacts_search(
            tg, op.query, use_global=op.use_global
        ),
        rows=identity_cmd.contacts_to_rows,
    ),
    "media.manifest": _Spec(
        cli=lambda a: MediaManifest(a.source, a.media_type, a.since, a.limit),
        batch=lambda p: MediaManifest(
            p["source"],
            _batch_choice(p.get("type"), "media.manifest.type", MEDIA_KINDS),
            _batch_when(p.get("since"), "media.manifest.since"),
            _batch_int(p.get("limit", 100), "media.manifest.limit"),
        ),
        fetch=lambda tg, op: media_cmd.manifest(
            tg, op.source, kind=op.kind, since=op.since, limit=op.limit
        ),
        rows=media_cmd.manifest_to_rows,
    ),
    "thread": _Spec(
        cli=lambda a: Thread(a.chat, a.message_id, a.depth, a.replies, a.limit),
        batch=lambda p: Thread(
            p["chat"],
            _batch_int(p["message_id"], "thread.message_id"),
            _batch_int(p.get("depth", 20), "thread.depth"),
            _batch_bool(p.get("replies"), "thread.replies"),
            _batch_int(p.get("limit", 50), "thread.limit"),
        ),
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
        batch=lambda p: Draft(p["chat"]),
        fetch=lambda tg, op: draft_cmd.fetch_show(tg, op.chat),
        rows=draft_cmd.show_to_rows,
    ),
    "draft.list": _Spec(
        cli=lambda a: Drafts(),
        batch=lambda p: Drafts(),
        fetch=lambda tg, op: draft_cmd.fetch_list(tg),
        rows=draft_cmd.list_to_rows,
    ),
}

BATCH_OP_NAMES = frozenset(_SPECS)


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


def from_batch(payload: dict[str, Any]) -> ReadOperation:
    op = payload["op"]
    spec = _SPECS.get(op)
    if spec is None:
        raise PolicyError(f"unhandled batch op: {op!r}")
    return spec.batch(payload)


async def execute(tg, operation: ReadOperation) -> Result:
    spec = _SPECS.get(operation.name)
    if spec is None:
        raise AssertionError(f"unhandled read operation: {operation!r}")
    data = await spec.fetch(tg, operation)
    return Result(data, spec.rows(data))
