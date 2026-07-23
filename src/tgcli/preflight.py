"""Everything that must hold before a session is opened.

`prepare` normalizes and validates a parsed namespace in place: argument shape,
time bounds, mutation gates, preview loading, and raw-API policy. It either
returns cleanly or fails closed — `parser.error` (SystemExit) for grammar-level
mistakes, `TgcliError` for policy ones. No network, no config.
"""

from __future__ import annotations

import argparse
import sys
from datetime import datetime

from tgcli import read_ops, safety
from tgcli.commands import api as api_cmd
from tgcli.commands import batch as batch_cmd
from tgcli.commands import dialog as dialog_cmd
from tgcli.errors import PolicyError


MUTATION_POSITIONALS = {
    "edit": ("chat", "message_id", "text"),
    "delete": ("chat", "message_id"),
    "forward": ("source", "message_id", "destination"),
}


def prepare(parser: argparse.ArgumentParser, args) -> None:
    _prepare_search(parser, args)
    _prepare_batch(args)
    _prepare_time_bounds(parser, args)
    _prepare_mutations(args)
    _prepare_previews(parser, args)
    _prepare_api(parser, args)


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
    if args.command not in ("read", "search") and not (
        args.command == "media" and args.media_command in ("manifest", "download")
    ):
        return
    args.since = _parse_when(parser, getattr(args, "since", None), "--since")
    if args.command != "media" or args.media_command == "manifest":
        args.until = _parse_when(parser, getattr(args, "until", None), "--until")


def _prepare_mutations(args) -> None:
    """Gate the write commands that carry no preview/commit handshake."""
    if args.command == "clone" and args.clone_command == "sync":
        safety.enforce_mutation_allowed(args.readonly)
        if args.limit is not None and args.limit <= 0:
            raise PolicyError("clone sync --limit must be positive")
    if args.command in ("mark-read", "mark-unread"):
        safety.enforce_mutation_allowed(args.readonly)
    if args.command == "dialog":
        safety.enforce_mutation_allowed(args.readonly)
        if args.dialog_command == "mute":
            dialog_cmd.validate_mute_flags(
                until=getattr(args, "until", None),
                forever=bool(getattr(args, "forever", False)),
            )


def _prepare_previews(parser: argparse.ArgumentParser, args) -> None:
    """Enforce the preview → commit handshake and load the committed payload."""
    if args.command == "send":
        if args.commit:
            if (
                args.preview
                or args.chat is not None
                or args.text is not None
                or args.reply_to is not None
                or args.file is not None
                or args.caption is not None
                or args.topic is not None
                or args.silent
            ):
                parser.error("send --commit accepts only a preview id")
            safety.enforce_mutation_allowed(args.readonly)
            args.preview_payload = safety.begin_commit(
                args.commit, expected_kind="send"
            )
        elif not (
            args.preview
            and args.chat is not None
            and (args.text is not None or args.file is not None)
        ):
            parser.error(
                "send requires CHAT (TEXT | --file PATH) --preview "
                "or --commit PREVIEW_ID"
            )
    elif args.command in MUTATION_POSITIONALS:
        names = MUTATION_POSITIONALS[args.command]
        values = [getattr(args, name) for name in names]
        if args.commit:
            if args.preview or any(value is not None for value in values):
                parser.error(f"{args.command} --commit accepts only a preview id")
            safety.enforce_mutation_allowed(args.readonly)
            args.preview_payload = safety.begin_commit(
                args.commit, expected_kind=args.command
            )
        elif not (args.preview and all(value is not None for value in values)):
            parser.error(
                f"{args.command} requires "
                f"{' '.join(name.upper() for name in names)} --preview "
                "or --commit PREVIEW_ID"
            )
    if args.command == "clone" and args.clone_command == "init" and args.commit:
        safety.enforce_mutation_allowed(args.readonly)
        args.preview_payload = safety.consume_preview(args.commit)
        if (
            args.preview_payload.get("kind") != "clone-init"
            or args.preview_payload.get("source") != args.source
        ):
            raise PolicyError("clone init preview does not match this source")


def _prepare_api(parser: argparse.ArgumentParser, args) -> None:
    if args.command != "api":
        return
    if args.write:
        safety.enforce_mutation_allowed(args.readonly)
        args.method = api_cmd.canonical_method(args.method)
        if api_cmd.is_hard_denied(args.method):
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
