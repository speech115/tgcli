"""`tg run`: a Python script with the authenticated client.

The long tail no wrapped command covers runs as ordinary Telethon code. The
script gets the same governed client every command uses, so pacing, flood
cooldowns, and the busy-session wait still apply. It may only read unless
`--write` is given; every write is audited before it leaves.
"""

from __future__ import annotations

import ast
import inspect
import sys
import traceback
from pathlib import Path

from telethon import errors as telethon_errors, types
from telethon.tl import functions
from telethon.tl.tlobject import TLRequest

from tgcli import safety
from tgcli.commands.read import message_to_dict
from tgcli.errors import ConfigError, PolicyError, TgcliError
from tgcli.formatting import mask_phones_in_text
from tgcli.output import note

READ_VERBS = ("get", "search", "check", "resolve")
# Getters with a side effect (ADR-0010's rejected list): pressing a bot button
# or querying an inline bot, publishing a location, counting a view, an ad
# impression.
SIDE_EFFECT_GETTERS = frozenset(
    {
        "contacts.getLocated",
        "messages.getBotCallbackAnswer",
        "messages.getInlineBotResults",
        "messages.getMessagesViews",
        "messages.getSponsoredMessages",
    }
)
# Telethon exports this session's authorization to another datacentre to
# download media stored there. It is session plumbing, not a user write.
PLUMBING = frozenset({"auth.exportAuthorization"})
# Credentials and account lifecycle stay with `tg accounts`: refused even to
# read, since auth.checkPassword and account.getTmpPassword only look like
# reads (ADR-0010, ADR-0092).
DENIED_NAMESPACES = frozenset({"account", "auth"})
# The root namespace is MTProto session plumbing: `destroyAuthKey` and
# `destroySession` end the session like a logout, the rest is key exchange.
# Only the pings are let through; invoke wrappers are unwrapped before this.
ROOT_ALLOWED = frozenset({"functions.ping", "functions.pingDelayDisconnect"})


# A getter that takes one of these acts on it: rotating a stream key
# (`revoke`), accepting or declining an offer (`decline`), a cloud-password
# check (`password`). It is a write whatever its name says.
WRITE_ARGUMENTS = frozenset({"decline", "password", "revoke"})


def _unwrapped(request):
    while type(request).__module__ == "telethon.tl.functions" and hasattr(
        request, "query"
    ):
        request = request.query
    return request


def _name(request_type: type) -> str:
    namespace = request_type.__module__.rsplit(".", 1)[-1]
    stem = request_type.__name__.removesuffix("Request")
    return f"{namespace}.{stem[:1].lower()}{stem[1:]}"


def method_name(request) -> str:
    """`namespace.method` for a Telethon request, unwrapping invoke wrappers."""
    return _name(type(_unwrapped(request)))


def is_read(request_type: type) -> bool:
    name = _name(request_type)
    namespace, _, method = name.partition(".")
    if namespace == "functions":
        return name in ROOT_ALLOWED
    if WRITE_ARGUMENTS & set(inspect.signature(request_type.__init__).parameters):
        return False
    return method.startswith(READ_VERBS) and name not in SIDE_EFFECT_GETTERS


def read_methods() -> list[str]:
    """Every pinned-Telethon method `tg run` lets through without --write."""
    found: set[str] = set()
    pending = [functions]
    while pending:
        module = pending.pop()
        for value in vars(module).values():
            if inspect.ismodule(value) and value.__name__.startswith(
                f"{functions.__name__}."
            ):
                pending.append(value)
            elif (
                inspect.isclass(value)
                and issubclass(value, TLRequest)
                and value is not TLRequest
                and _name(value).partition(".")[0] not in DENIED_NAMESPACES
                and is_read(value)
            ):
                found.add(_name(value))
    return sorted(found)


def install_write_gate(client, account: str, *, write: bool) -> None:
    """Refuse writes unless `--write`; audit each allowed write first."""
    inner = client._call

    async def gated(sender, request, *args, **kwargs):
        requests = request if isinstance(request, list) else [request]
        for item in requests:
            name = method_name(item)
            if name in PLUMBING:
                continue
            namespace = name.partition(".")[0]
            if namespace in DENIED_NAMESPACES:
                raise PolicyError(f"{name} is never allowed in tg run; use tg accounts")
            if namespace == "functions" and name not in ROOT_ALLOWED:
                raise PolicyError(
                    f"{name} is never allowed in tg run: it is session plumbing"
                )
            if is_read(type(_unwrapped(item))):
                continue
            if not write:
                raise PolicyError(
                    f"tg run is read-only: {name} writes; re-run with --write"
                )
            safety.append_audit("run-write", account, {"method": name})
        return await inner(sender, request, *args, **kwargs)

    client._call = gated


def read_source(script: str) -> tuple[str, str]:
    if script == "-":
        return "<stdin>", sys.stdin.read()
    path = Path(script).expanduser().resolve()
    try:
        return str(path), path.read_text(encoding="utf-8")
    except (OSError, UnicodeError) as exc:
        raise ConfigError(f"cannot read script {path}: {exc}") from exc


def compile_source(script: str):
    filename, source = read_source(script)
    if not source.strip():
        raise ConfigError("script is empty")
    try:
        return compile(source, filename, "exec", flags=ast.PyCF_ALLOW_TOP_LEVEL_AWAIT)
    except SyntaxError as exc:
        raise ConfigError(f"script does not compile: {exc}") from exc


async def run(client, code, account: str, *, argv: list[str], write: bool) -> dict:
    install_write_gate(client, account, write=write)
    namespace = {
        "__name__": "__main__",
        "__file__": code.co_filename,
        "client": client,
        "functions": functions,
        "types": types,
        "account": account,
        "msg": message_to_dict,
    }
    previous_argv = sys.argv
    sys.argv = [code.co_filename, *argv]
    try:
        result = eval(code, namespace)
        if inspect.isawaitable(result):
            await result
    except SystemExit as exc:
        if exc.code not in (None, 0):
            raise TgcliError(f"script exited with status {exc.code}") from None
    except (TgcliError, telethon_errors.RPCError):
        raise
    except Exception:
        # The script's own bug: its traceback is what the agent fixes it from.
        note(mask_phones_in_text(traceback.format_exc()).rstrip())
        raise
    finally:
        sys.argv = previous_argv
    return {}
