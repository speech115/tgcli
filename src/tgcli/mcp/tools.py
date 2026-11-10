"""The adapter's tools: read-only `tg` commands behind a chat allowlist.

Each tool validates its arguments strictly, refuses any chat that is not on
the allowlist, and turns the call into one `tg` argv. Options come first and
positionals follow a ``--`` separator, so no argument value can be read as a
flag.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

_USERNAME = re.compile(r"^@?([A-Za-z][A-Za-z0-9_]{2,63})$")
_LINK = re.compile(
    r"^(?:https?://)?(?:www\.)?(?:t|telegram)\.me/(?:s/)?(c/)?([A-Za-z0-9_]+)(?:[/?#].*)?$",
    re.IGNORECASE,
)
_NUMERIC = re.compile(r"^-?\d{1,20}$")


class ToolError(Exception):
    """A call the adapter refuses before running `tg`."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


def chat_keys(ref: str | int) -> set[str]:
    """Every allowlist key a chat reference stands for; empty if unparseable.

    ``@Name``, ``name`` and ``t.me/name`` all mean ``@name``. A numeric id is
    compared whole: tg resolves a positive id as a user, so ``-1001234`` (a
    channel) and ``1234`` (a user) are different chats. ``t.me/c/1234`` is the
    channel ``-1001234``.
    """
    text = str(ref).strip()
    if _NUMERIC.match(text):
        return {str(int(text))}
    link = _LINK.match(text)
    if link:
        private, slug = link.groups()
        if private:
            return {f"-100{int(slug)}"} if slug.isdigit() else set()
        text = slug
    username = _USERNAME.match(text)
    if username:
        return {"@" + username.group(1).lower()}
    return set()


@dataclass(frozen=True)
class Allowlist:
    keys: frozenset[str]
    allow_all: bool = False

    @classmethod
    def build(cls, entries: list[str], allow_all: bool = False) -> Allowlist:
        keys: set[str] = set()
        for entry in entries:
            parsed = chat_keys(entry)
            if not parsed:
                raise ValueError(f"not a chat reference: {entry!r}")
            keys |= parsed
        return cls(frozenset(keys), allow_all)

    def allows(self, ref: str | int) -> bool:
        return self.allow_all or bool(chat_keys(ref) & self.keys)

    def allows_row(self, row: dict[str, Any]) -> bool:
        if self.allow_all:
            return True
        refs: list[str | int] = []
        if isinstance(row.get("id"), int):
            refs.append(row["id"])
        if isinstance(row.get("username"), str):
            refs.append(row["username"])
        return any(self.allows(ref) for ref in refs)


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    schema: dict[str, Any]
    build: Callable[[dict[str, Any], Allowlist], list[str]]

    def listing(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "inputSchema": self.schema,
            "annotations": {"readOnlyHint": True, "openWorldHint": True},
        }


def _schema(properties: dict[str, Any], required: list[str]) -> dict[str, Any]:
    return {
        "type": "object",
        "properties": properties,
        "required": required,
        "additionalProperties": False,
    }


_CHAT = {
    "type": "string",
    "description": "Allowlisted chat: @username, t.me link, or numeric id.",
}


def _int(name: str, low: int, high: int, default: int | None = None) -> dict:
    spec: dict[str, Any] = {"type": "integer", "minimum": low, "maximum": high}
    if default is not None:
        spec["default"] = default
    spec["description"] = name
    return spec


def _check(args: dict[str, Any], schema: dict[str, Any]) -> dict[str, Any]:
    """Validate ``args`` against the small schema subset the tools use."""
    if not isinstance(args, dict):
        raise ToolError("INVALID_ARGUMENTS", "arguments must be an object")
    properties = schema["properties"]
    unknown = sorted(set(args) - set(properties))
    if unknown:
        raise ToolError("INVALID_ARGUMENTS", f"unknown arguments: {unknown}")
    for name in schema["required"]:
        if name not in args:
            raise ToolError("INVALID_ARGUMENTS", f"missing argument: {name}")
    values: dict[str, Any] = {}
    for name, spec in properties.items():
        if name not in args:
            if "default" in spec:
                values[name] = spec["default"]
            continue
        value = args[name]
        kind = spec["type"]
        if kind == "integer":
            # bool is an int subclass; a JSON true is not a count.
            if isinstance(value, bool) or not isinstance(value, int):
                raise ToolError("INVALID_ARGUMENTS", f"{name} must be an integer")
            if not spec["minimum"] <= value <= spec["maximum"]:
                raise ToolError(
                    "INVALID_ARGUMENTS",
                    f"{name} must be in {spec['minimum']}..{spec['maximum']}",
                )
        elif kind == "boolean":
            if not isinstance(value, bool):
                raise ToolError("INVALID_ARGUMENTS", f"{name} must be a boolean")
        elif kind == "string":
            if not isinstance(value, str) or not value.strip():
                raise ToolError("INVALID_ARGUMENTS", f"{name} must be a string")
            if "enum" in spec and value not in spec["enum"]:
                raise ToolError("INVALID_ARGUMENTS", f"{name} must be {spec['enum']}")
        values[name] = value
    return values


def _chat(values: dict[str, Any], allowlist: Allowlist) -> str:
    chat = values["chat"].strip()
    if not chat_keys(chat):
        raise ToolError("INVALID_ARGUMENTS", "chat is not a chat reference")
    if not allowlist.allows(chat):
        raise ToolError("CHAT_NOT_ALLOWED", f"chat {chat!r} is not on the allowlist")
    return chat


def _options(pairs: list[tuple[str, Any]]) -> list[str]:
    argv: list[str] = []
    for flag, value in pairs:
        if value is None or value is False:
            continue
        argv += [flag] if value is True else [flag, str(value)]
    return argv


def _dialogs(args: dict[str, Any], allowlist: Allowlist) -> list[str]:
    values = _check(args, DIALOGS.schema)
    return [
        "dialogs",
        *_options(
            [
                ("--limit", values["limit"]),
                ("--unread-only", values.get("unread_only", False)),
                ("--kind", values.get("kind")),
            ]
        ),
    ]


def _read(args: dict[str, Any], allowlist: Allowlist) -> list[str]:
    values = _check(args, READ.schema)
    chat = _chat(values, allowlist)
    options = [
        ("--limit", values["limit"]),
        ("--before-id", values.get("before_id")),
        ("--after-id", values.get("after_id")),
    ]
    return ["read", *_options(options), "--", chat]


def _search(args: dict[str, Any], allowlist: Allowlist) -> list[str]:
    values = _check(args, SEARCH.schema)
    chat = _chat(values, allowlist)
    return ["search", *_options([("--limit", values["limit"])]), "--", chat] + [
        values["query"]
    ]


def _message(args: dict[str, Any], allowlist: Allowlist) -> list[str]:
    values = _check(args, MESSAGE.schema)
    chat = _chat(values, allowlist)
    options = _options([("--context", values.get("context") or None)])
    return ["message", *options, "--", chat, str(values["message_id"])]


def _info(args: dict[str, Any], allowlist: Allowlist) -> list[str]:
    values = _check(args, INFO.schema)
    chat = _chat(values, allowlist)
    return ["info", "--", chat]


_ID = 2**53

DIALOGS = Tool(
    "telegram_dialogs",
    "List Telegram dialogs, newest first. Without --allow-all-chats only "
    "allowlisted chats are returned.",
    _schema(
        {
            "limit": _int("Rows to return.", 1, 200, 50),
            "unread_only": {"type": "boolean", "description": "Only unread."},
            "kind": {
                "type": "string",
                "enum": ["user", "group", "channel"],
                "description": "Dialog kind filter.",
            },
        },
        [],
    ),
    _dialogs,
)
READ = Tool(
    "telegram_read",
    "Read recent messages from one allowlisted chat, newest first.",
    _schema(
        {
            "chat": _CHAT,
            "limit": _int("Messages to return.", 1, 100, 20),
            "before_id": _int("Only messages older than this id.", 1, _ID),
            "after_id": _int("Only messages newer than this id.", 1, _ID),
        },
        ["chat"],
    ),
    _read,
)
SEARCH = Tool(
    "telegram_search",
    "Search one allowlisted chat for a text query, newest first.",
    _schema(
        {
            "chat": _CHAT,
            "query": {"type": "string", "description": "Text to search for."},
            "limit": _int("Hits to return.", 1, 100, 20),
        },
        ["chat", "query"],
    ),
    _search,
)
MESSAGE = Tool(
    "telegram_message",
    "Read one message by id from an allowlisted chat, optionally with "
    "neighbouring messages.",
    _schema(
        {
            "chat": _CHAT,
            "message_id": _int("Message id.", 1, _ID),
            "context": _int("Neighbours on each side.", 0, 10),
        },
        ["chat", "message_id"],
    ),
    _message,
)
INFO = Tool(
    "telegram_info",
    "Show name, kind and username of one allowlisted chat.",
    _schema({"chat": _CHAT}, ["chat"]),
    _info,
)

TOOLS: dict[str, Tool] = {
    tool.name: tool for tool in (DIALOGS, READ, SEARCH, MESSAGE, INFO)
}
