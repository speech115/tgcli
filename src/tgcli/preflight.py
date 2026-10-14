"""Everything that must hold before a session is opened.

`prepare` normalizes and validates a parsed namespace in place: time bounds,
the readonly gate, preview loading, and each command's own argument checks.
It either returns cleanly or fails closed — `parser.error` (SystemExit) for
grammar-level mistakes, `TgcliError` for policy ones. No network, no config.

Which gate and which checks apply is declared per command in
`tgcli.registry`; the hooks it names live here.
"""

from __future__ import annotations

import argparse
import math
from collections.abc import Callable
from datetime import datetime
from typing import TYPE_CHECKING

from tgcli import preview_commit, read_ops, safety
from tgcli.commands import (
    run as run_cmd,
)
from tgcli.errors import PolicyError

if TYPE_CHECKING:
    from tgcli.registry import Command

Hook = Callable[[argparse.ArgumentParser, argparse.Namespace], None]


def prepare(parser: argparse.ArgumentParser, args, spec: Command) -> None:
    _prepare_session_role(args)
    _prepare_max_runtime(args)
    _enforce_gate(args, spec)
    preview_commit.prepare(parser, args)
    if spec.prepare is not None:
        spec.prepare(parser, args)


def _enforce_gate(args, spec: Command) -> None:
    """The readonly gate a command declares; handshakes gate their commit."""
    if not spec.gated(args):
        return
    if spec.gate == "telegram":
        safety.enforce_mutation_allowed(args.readonly)
    elif spec.gate == "local":
        safety.enforce_local_mutation_allowed(args.readonly)


def _prepare_max_runtime(args) -> None:
    """--max-runtime is a wall-clock cap: non-positive values are misuse."""
    cap = getattr(args, "max_runtime", None)
    if cap is not None and (not math.isfinite(cap) or cap <= 0):
        raise PolicyError("--max-runtime must be a positive finite number of seconds")
    # --timeout is a hang detector; a non-positive deadline is meaningless
    # and behaves differently for local vs network commands (review D2).
    timeout = getattr(args, "timeout", None)
    if timeout is not None and (not math.isfinite(timeout) or timeout <= 0):
        raise PolicyError("--timeout must be a positive finite number of seconds")


def prepare_run(parser: argparse.ArgumentParser, args) -> None:
    """Compile the script before any session work; `--write` is gated first."""
    args.run_code = run_cmd.compile_source(args.script)


def _prepare_session_role(args) -> None:
    role = getattr(args, "session_role", None)
    if role is None:
        return
    from tgcli.config import validate_role_name

    validate_role_name(role)


def _parse_when(
    parser: argparse.ArgumentParser, value: str | None, flag: str
) -> datetime | None:
    def invalid():
        parser.error(f"{flag} expects an ISO 8601 date or datetime")

    return read_ops.parse_when(value, invalid=invalid)


def prepare_search(parser: argparse.ArgumentParser, args) -> None:
    if args.all:
        if args.query is not None or args.chat is None:
            parser.error("search --all takes exactly one QUERY")
        if args.from_user is not None or args.since is not None:
            parser.error("search --all only supports QUERY and --limit")
        args.query, args.chat = args.chat, None
    elif args.chat is None or args.query is None:
        parser.error("search requires CHAT QUERY (or --all QUERY)")
    bounds("since", "until")(parser, args)


_BOUND_FLAGS = {
    "since": "--since",
    "until": "--until",
    "around_date": "--around-date",
}


def bounds(*names: str) -> Hook:
    """A hook that parses the named time-bound flags to datetimes."""

    def parse(parser: argparse.ArgumentParser, args) -> None:
        for name in names:
            setattr(
                args,
                name,
                _parse_when(parser, getattr(args, name, None), _BOUND_FLAGS[name]),
            )

    return parse


def prepare_clone_sync(parser: argparse.ArgumentParser, args) -> None:
    if args.limit is not None and args.limit <= 0:
        raise PolicyError("clone sync --limit must be positive")


def prepare_login(parser: argparse.ArgumentParser, args) -> None:
    """Validate login flag combinations (the local gate runs first).

    TGCLI_NO_SEND deliberately does not apply (ADR-0042 §8).
    """
    continue_id = getattr(args, "continue_id", None)
    if continue_id:
        if args.alias is not None:
            raise PolicyError("accounts login --continue takes no ALIAS")
        for flag, value in (
            ("--phone", getattr(args, "phone", None)),
            ("--api-id", getattr(args, "api_id", None)),
            ("--api-hash", getattr(args, "api_hash", None)),
        ):
            if value is not None:
                raise PolicyError(f"accounts login --continue rejects {flag}")
        if getattr(args, "force", False):
            raise PolicyError("accounts login --continue rejects --force")
        if getattr(args, "login_role", None) is not None:
            raise PolicyError("accounts login --continue rejects --role")
        return
    if args.alias is None:
        raise PolicyError("accounts login requires ALIAS (or --continue LOGIN_ID)")
    if getattr(args, "code", None) is not None:
        raise PolicyError("accounts login rejects --code without --continue")
    if getattr(args, "password_stdin", False):
        raise PolicyError("accounts login rejects --password-stdin without --continue")
    phone = getattr(args, "phone", None)
    if not isinstance(phone, str) or not phone.strip():
        if phone is not None:
            raise PolicyError("accounts login --phone must be non-empty")
        raise PolicyError("accounts login requires --phone PHONE")
    args.phone = phone.strip()
    api_id = getattr(args, "api_id", None)
    api_hash = getattr(args, "api_hash", None)
    if (api_id is None) ^ (api_hash is None):
        raise PolicyError("--api-id and --api-hash are required together")
    login_role = getattr(args, "login_role", None)
    if login_role is not None:
        from tgcli.config import validate_role_name

        validate_role_name(login_role)
