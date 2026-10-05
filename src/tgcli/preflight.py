"""Everything that must hold before a session is opened.

`prepare` normalizes and validates a parsed namespace in place: argument shape,
time bounds, mutation gates, preview loading, and raw-API policy. It either
returns cleanly or fails closed — `parser.error` (SystemExit) for grammar-level
mistakes, `TgcliError` for policy ones. No network, no config.
"""

from __future__ import annotations

import argparse
import math
from datetime import datetime

from tgcli import preview_commit, read_ops, safety
from tgcli.archive import preflight as archive_preflight
from tgcli.commands import (
    run as run_cmd,
)
from tgcli.errors import PolicyError


def prepare(parser: argparse.ArgumentParser, args) -> None:
    _prepare_session_role(args)
    _prepare_max_runtime(args)
    _prepare_search(parser, args)
    _prepare_time_bounds(parser, args)
    _prepare_mutations(args)
    preview_commit.prepare(parser, args)
    _prepare_run(args)
    archive_preflight.prepare(args)


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


def _prepare_run(args) -> None:
    """Compile the script and gate --write before any session work."""
    if args.command != "run":
        return
    if args.write:
        safety.enforce_mutation_allowed(args.readonly)
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


def _prepare_search(parser: argparse.ArgumentParser, args) -> None:
    if args.command != "search":
        return
    if args.all:
        if args.query is not None or args.chat is None:
            parser.error("search --all takes exactly one QUERY")
        if args.from_user is not None or args.since is not None:
            parser.error("search --all only supports QUERY and --limit")
        args.query, args.chat = args.chat, None
    elif args.chat is None or args.query is None:
        parser.error("search requires CHAT QUERY (or --all QUERY)")


def _prepare_time_bounds(parser: argparse.ArgumentParser, args) -> None:
    if (
        args.command not in ("read", "search")
        and not archive_preflight.uses_time_bounds(args)
        and not (
            args.command == "media" and args.media_command in ("manifest", "download")
        )
    ):
        return
    args.since = _parse_when(parser, getattr(args, "since", None), "--since")
    if args.command != "media" or args.media_command == "manifest":
        args.until = _parse_when(parser, getattr(args, "until", None), "--until")
    if args.command == "archive" and args.archive_command == "read":
        args.around_date = _parse_when(
            parser, getattr(args, "around_date", None), "--around-date"
        )


def _prepare_mutations(args) -> None:
    """Gate the write commands that carry no preview/commit handshake."""
    if args.command == "clone" and args.clone_command == "sync":
        safety.enforce_mutation_allowed(args.readonly)
        if args.limit is not None and args.limit <= 0:
            raise PolicyError("clone sync --limit must be positive")
    if args.command == "transcribe":
        # transcribeAudio is a server-side mutation (Premium quota, visible
        # to other clients): the --readonly gate applies (ADR-0079).
        safety.enforce_mutation_allowed(args.readonly)
    if args.command == "accounts" and args.subcommand == "login":
        _prepare_login(args)


def _prepare_login(args) -> None:
    """Validate login flag combinations; gate with local-mutation readonly."""
    # TGCLI_NO_SEND deliberately does not apply (ADR-0042 §8).
    safety.enforce_local_mutation_allowed(args.readonly)
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
