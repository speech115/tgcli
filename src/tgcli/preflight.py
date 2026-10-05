"""Everything that must hold before a session is opened.

`prepare` normalizes and validates a parsed namespace in place: argument shape,
time bounds, mutation gates, preview loading, and raw-API policy. It either
returns cleanly or fails closed — `parser.error` (SystemExit) for grammar-level
mistakes, `TgcliError` for policy ones. No network, no config.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
from datetime import datetime

from tgcli import preview_commit, read_ops, safety
from tgcli.archive import preflight as archive_preflight
from tgcli.commands import api as api_cmd, batch as batch_cmd, dialog as dialog_cmd
from tgcli.errors import ConfigError, PolicyError


def prepare(parser: argparse.ArgumentParser, args) -> None:
    _prepare_session_role(args)
    _prepare_max_runtime(args)
    _prepare_search(parser, args)
    _prepare_batch(args)
    _prepare_time_bounds(parser, args)
    _prepare_mutations(args)
    preview_commit.prepare(parser, args)
    _prepare_api(parser, args)
    _prepare_changes(args)
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


def _prepare_changes(args) -> None:
    if args.command != "changes":
        return
    init = bool(getattr(args, "init", False))
    cursor = getattr(args, "changes_cursor", None)
    wait = getattr(args, "changes_wait", None)
    drop = getattr(args, "changes_drop_peers", None) or []
    peers = getattr(args, "changes_peers", None) or []
    if any(not p for p in peers) or any(not p for p in drop):
        raise PolicyError("changes --peer/--drop-peer values must be non-empty")
    if init and cursor is not None:
        raise PolicyError("changes --init rejects --cursor; start a new baseline")
    if init and drop:
        raise PolicyError("changes --init rejects --drop-peer")
    if init and wait is not None:
        raise PolicyError("changes --init rejects --wait")
    if not init and cursor is None:
        raise PolicyError("changes cursor is required; run: tg changes --init")
    if wait is not None and wait <= 0:
        raise PolicyError("--wait must be a positive number of seconds")


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


def _prepare_batch(args) -> None:
    if args.command != "batch":
        return
    args.batch_lines = sys.stdin.read().splitlines()
    # Fail closed on allowlist/cap before opening a session.
    batch_cmd.parse_ops(args.batch_lines)


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
    if args.command in ("mark-read", "mark-unread"):
        safety.enforce_mutation_allowed(args.readonly)
    if args.command == "transcribe":
        # transcribeAudio is a server-side mutation (Premium quota, visible
        # to other clients): the --readonly gate applies (ADR-0079).
        safety.enforce_mutation_allowed(args.readonly)
    if args.command == "dialog":
        safety.enforce_mutation_allowed(args.readonly)
        if args.dialog_command == "mute":
            dialog_cmd.validate_mute_flags(
                until=getattr(args, "until", None),
                forever=bool(getattr(args, "forever", False)),
            )
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


def _prepare_api(parser: argparse.ArgumentParser, args) -> None:
    if args.command != "api":
        return
    if args.write:
        safety.enforce_mutation_allowed(args.readonly)
        args.method = api_cmd.canonical_method(args.method)
        if api_cmd.is_hard_denied(args.method) or api_cmd.is_namespace_denied(
            args.method
        ):
            raise PolicyError("raw API method is permanently denied")
        confirm = (
            api_cmd.try_canonical_method(args.confirm) if args.confirm else args.confirm
        )
        if api_cmd.requires_confirmation(args.method) and confirm != args.method:
            raise PolicyError(
                "raw API destructive write requires exact --confirm METHOD"
            )
    else:
        canonical = api_cmd.try_canonical_method(args.method)
        if canonical is None or not api_cmd.is_read_method(canonical):
            raise PolicyError("raw API method is not allowlisted for read-only use")
        args.method = canonical
    if args.params is None:
        parser.error("the following arguments are required: --params")
    # A JSON typo is a purely local mistake: catch it here rather than after
    # config loading and a session open (the request builder re-checks).
    try:
        params = json.loads(args.params)
    except json.JSONDecodeError as exc:
        raise ConfigError("raw API params must be valid JSON") from exc
    if not isinstance(params, dict):
        raise ConfigError("raw API params must be a JSON object")
