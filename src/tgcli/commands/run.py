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

from tgcli import safety
from tgcli.commands.read import message_to_dict
from tgcli.errors import ConfigError, PolicyError, TgcliError
from tgcli.formatting import mask_phones_in_text
from tgcli.output import note

READ_VERBS = ("get", "search", "check", "resolve")
# Getters with a side effect: pressing a bot button, publishing a location,
# counting a view.
SIDE_EFFECT_GETTERS = frozenset(
    {
        "contacts.getLocated",
        "messages.getBotCallbackAnswer",
        "messages.getMessagesViews",
    }
)
# Telethon exports this session's authorization to another datacentre to
# download media stored there. It is session plumbing, not a user write.
PLUMBING = frozenset({"auth.exportAuthorization"})
# Session and account lifecycle stays with `tg accounts` (ADR-0092).
DENIED_NAMESPACES = frozenset({"account", "auth"})


def method_name(request) -> str:
    """`namespace.method` for a Telethon request, unwrapping invoke wrappers."""
    while type(request).__module__ == "telethon.tl.functions" and hasattr(
        request, "query"
    ):
        request = request.query
    namespace = type(request).__module__.rsplit(".", 1)[-1]
    stem = type(request).__name__.removesuffix("Request")
    return f"{namespace}.{stem[:1].lower()}{stem[1:]}"


def is_read(name: str) -> bool:
    if name in PLUMBING:
        return True
    namespace, _, method = name.partition(".")
    if namespace == "functions":
        return method in ("ping", "pingDelayDisconnect")
    return method.startswith(READ_VERBS) and name not in SIDE_EFFECT_GETTERS


def install_write_gate(client, account: str, *, write: bool) -> None:
    """Refuse writes unless `--write`; audit each allowed write first."""
    inner = client._call

    async def gated(sender, request, *args, **kwargs):
        requests = request if isinstance(request, list) else [request]
        for item in requests:
            name = method_name(item)
            if is_read(name):
                continue
            if not write:
                raise PolicyError(
                    f"tg run is read-only: {name} writes; re-run with --write"
                )
            if name.partition(".")[0] in DENIED_NAMESPACES:
                raise PolicyError(f"{name} is never allowed; use tg accounts")
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
