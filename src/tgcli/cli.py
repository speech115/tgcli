import argparse
import asyncio
import logging
import sys
import time
from datetime import UTC, datetime
from pathlib import Path

from telethon import errors as telethon_errors

from tgcli import __version__, invocations, output, safety, session
from tgcli.commands import accounts as accounts_cmd
from tgcli.commands import api as api_cmd
from tgcli.commands import clone as clone_cmd
from tgcli.commands import dialog as dialog_cmd
from tgcli.commands import dialogs as dialogs_cmd
from tgcli.commands import doctor as doctor_cmd
from tgcli.commands import export as export_cmd
from tgcli.commands import identity as identity_cmd
from tgcli.commands import info as info_cmd
from tgcli.commands import media as media_cmd
from tgcli.commands import mutate as mutate_cmd
from tgcli.commands import read as read_cmd
from tgcli.commands import search as search_cmd
from tgcli.commands import send as send_cmd
from tgcli.commands import thread as thread_cmd
from tgcli.config import load_config, resolve_account
from tgcli.errors import PolicyError, RateLimitError, TgcliError


LOGGER = logging.getLogger(__name__)


def _parse_when(
    parser: argparse.ArgumentParser, value: str | None, flag: str
) -> datetime | None:
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value)
    except ValueError:
        parser.error(f"{flag} expects an ISO 8601 date or datetime")
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed


def _enable_verbose_diagnostics():
    configured = []
    for name in ("tgcli", "telethon"):
        logger = logging.getLogger(name)
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(logging.Formatter("%(levelname)s %(name)s: %(message)s"))
        configured.append((logger, logger.level, logger.propagate, handler))
        logger.setLevel(logging.DEBUG)
        logger.propagate = False
        logger.addHandler(handler)
    return configured


def _restore_diagnostics(configured) -> None:
    for logger, level, propagate, handler in configured:
        logger.removeHandler(handler)
        logger.setLevel(level)
        logger.propagate = propagate


def build_parser() -> argparse.ArgumentParser:
    global_flags = argparse.ArgumentParser(
        add_help=False, argument_default=argparse.SUPPRESS
    )
    global_flags.add_argument("--account", help="account alias from config")
    global_flags.add_argument("--json", action="store_true", help="JSON to stdout")
    global_flags.add_argument("--plain", action="store_true", help="TSV to stdout")
    global_flags.add_argument("--readonly", action="store_true")
    global_flags.add_argument("--timeout", type=float)
    global_flags.add_argument("-v", "--verbose", action="store_true")

    parser = argparse.ArgumentParser(
        prog="tg", description="Stateless Telegram CLI", parents=[global_flags]
    )
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    p_accounts = sub.add_parser(
        "accounts", help="Manage accounts", parents=[global_flags]
    )
    accounts_sub = p_accounts.add_subparsers(dest="subcommand", required=True)
    accounts_sub.add_parser(
        "list", help="List configured accounts", parents=[global_flags]
    )
    p_import = accounts_sub.add_parser(
        "import",
        help="Copy authorized sessions from the old stack",
        parents=[global_flags],
    )
    p_import.add_argument("aliases", nargs="*", metavar="ALIAS")
    p_import.add_argument("--source-root", type=Path, default=Path("~"))
    p_import.add_argument("--force", action="store_true")

    p_dialogs = sub.add_parser("dialogs", help="List dialogs", parents=[global_flags])
    p_dialogs.add_argument("--limit", type=int, default=50)
    p_dialogs.add_argument("--unread-only", action="store_true")
    p_dialogs.add_argument("--kind", choices=["user", "group", "channel"])

    sub.add_parser(
        "doctor", help="Check environment and session health", parents=[global_flags]
    )

    p_read = sub.add_parser(
        "read", help="Read recent messages from a dialog", parents=[global_flags]
    )
    p_read.add_argument("chat", help="@username, t.me link, or dialog id")
    p_read.add_argument("--limit", type=int, default=20)
    p_read.add_argument(
        "--before-id", type=int, help="only messages older than this id"
    )
    p_read.add_argument("--after-id", type=int, help="only messages newer than this id")
    p_read.add_argument("--since", help="ISO date/datetime lower bound")
    p_read.add_argument("--until", help="ISO date/datetime upper bound")
    p_read.add_argument("--topic", type=int, help="forum topic id")

    p_search = sub.add_parser(
        "search", help="Search messages in a dialog", parents=[global_flags]
    )
    p_search.add_argument("chat", nargs="?", help="@username, t.me link, or dialog id")
    p_search.add_argument("query", nargs="?")
    p_search.add_argument("--all", action="store_true")
    p_search.add_argument("--limit", type=int, default=20)
    p_search.add_argument("--from", dest="from_user")
    p_search.add_argument("--since", help="ISO date/datetime lower bound")

    p_latest = sub.add_parser(
        "latest", help="Read the latest dialog message", parents=[global_flags]
    )
    p_latest.add_argument("chat", help="@username, t.me link, or dialog id")

    p_message = sub.add_parser(
        "message", help="Read one message by id", parents=[global_flags]
    )
    p_message.add_argument("chat", help="@username, t.me link, or dialog id")
    p_message.add_argument("message_id", type=int)
    p_message.add_argument("--context", type=int, default=0)

    p_info = sub.add_parser("info", help="Show dialog metadata", parents=[global_flags])
    p_info.add_argument("chat", help="@username, t.me link, or dialog id")
    p_info.add_argument("--full", action="store_true")

    p_count = sub.add_parser(
        "count", help="Count dialog messages", parents=[global_flags]
    )
    p_count.add_argument("chat", help="@username, t.me link, or dialog id")

    p_resolve = sub.add_parser(
        "resolve",
        help="Resolve a phone, @username, link, or id to a peer",
        parents=[global_flags],
    )
    p_resolve.add_argument("ref", help="+phone, @username, t.me link, or dialog id")

    p_thread = sub.add_parser(
        "thread",
        help="Read a reply chain (ancestors; optional replies)",
        parents=[global_flags],
    )
    p_thread.add_argument("chat")
    p_thread.add_argument("message_id", type=int)
    p_thread.add_argument(
        "--replies",
        action="store_true",
        help="include comment/forum replies when a cheap thread API exists",
    )
    p_thread.add_argument(
        "--depth",
        type=int,
        default=20,
        help="max ancestor steps (default 20, hard cap 100)",
    )
    p_thread.add_argument(
        "--limit",
        type=int,
        default=50,
        help="max replies when --replies is set (default 50)",
    )

    p_contacts = sub.add_parser(
        "contacts", help="List or search Telegram contacts", parents=[global_flags]
    )
    contacts_sub = p_contacts.add_subparsers(dest="contacts_command", required=True)
    contacts_sub.add_parser("list", parents=[global_flags])
    p_contacts_search = contacts_sub.add_parser("search", parents=[global_flags])
    p_contacts_search.add_argument(
        "query", help="case-insensitive substring over name/username"
    )
    p_contacts_search.add_argument(
        "--global",
        action="store_true",
        dest="use_global",
        help="search Telegram's global directory instead of local contacts",
    )

    p_media = sub.add_parser(
        "media", help="Inspect or download message media", parents=[global_flags]
    )
    media_sub = p_media.add_subparsers(dest="media_command", required=True)
    p_download = media_sub.add_parser("download", parents=[global_flags])
    p_download.add_argument("source", help="t.me link or chat reference")
    p_download.add_argument("message_id", nargs="?", type=int)
    p_download.add_argument("--output", help="final output path")
    p_download.add_argument("--parallel", type=int, default=1)
    p_manifest = media_sub.add_parser(
        "manifest",
        help="List media in a chat without downloading",
        parents=[global_flags],
    )
    p_manifest.add_argument("source", help="@username, t.me link, or dialog id")
    p_manifest.add_argument(
        "--type",
        dest="media_type",
        choices=list(media_cmd.MEDIA_KINDS),
        help="keep only this media kind",
    )
    p_manifest.add_argument("--since", help="ISO 8601 lower bound on message date")
    p_manifest.add_argument("--limit", type=int, default=100)

    p_send = sub.add_parser(
        "send", help="Preview and commit a message", parents=[global_flags]
    )
    p_send.add_argument("chat", nargs="?", help="target for --preview")
    p_send.add_argument("text", nargs="?", help="message text for --preview")
    p_send.add_argument(
        "--format",
        choices=("plain", "md", "html"),
        default="md",
        dest="format",
        help="rich-text format of TEXT/caption (html supports quote/spoiler/custom emoji)",
    )
    p_send.add_argument("--preview", action="store_true")
    p_send.add_argument("--commit", metavar="PREVIEW_ID")
    p_send.add_argument("--reply-to", type=int, dest="reply_to")
    p_send.add_argument("--file")
    p_send.add_argument("--caption")
    p_send.add_argument("--topic", type=int)
    p_send.add_argument("--silent", action="store_true")

    p_edit = sub.add_parser(
        "edit", help="Preview and commit a message edit", parents=[global_flags]
    )
    p_edit.add_argument("chat", nargs="?")
    p_edit.add_argument("message_id", nargs="?", type=int)
    p_edit.add_argument("text", nargs="?")
    p_edit.add_argument(
        "--format",
        choices=("plain", "md", "html"),
        default="plain",
        dest="format",
        help="rich-text format of TEXT (html supports quote/spoiler/custom emoji)",
    )
    p_edit.add_argument("--preview", action="store_true")
    p_edit.add_argument("--commit", metavar="PREVIEW_ID")

    p_delete = sub.add_parser(
        "delete", help="Preview and commit a message deletion", parents=[global_flags]
    )
    p_delete.add_argument("chat", nargs="?")
    p_delete.add_argument("message_id", nargs="?", type=int)
    p_delete.add_argument("--preview", action="store_true")
    p_delete.add_argument("--commit", metavar="PREVIEW_ID")

    p_forward = sub.add_parser(
        "forward", help="Preview and commit a forward", parents=[global_flags]
    )
    p_forward.add_argument("source", nargs="?")
    p_forward.add_argument("message_id", nargs="?", type=int)
    p_forward.add_argument("destination", nargs="?")
    p_forward.add_argument("--preview", action="store_true")
    p_forward.add_argument("--commit", metavar="PREVIEW_ID")

    p_mark_read = sub.add_parser(
        "mark-read", help="Mark a dialog as read", parents=[global_flags]
    )
    p_mark_read.add_argument("chat")

    p_mark_unread = sub.add_parser(
        "mark-unread", help="Mark a dialog as unread", parents=[global_flags]
    )
    p_mark_unread.add_argument("chat")

    p_dialog = sub.add_parser(
        "dialog", help="Change inbox dialog state", parents=[global_flags]
    )
    dialog_sub = p_dialog.add_subparsers(dest="dialog_command", required=True)
    p_dialog_pin = dialog_sub.add_parser(
        "pin", help="Pin a dialog", parents=[global_flags]
    )
    p_dialog_pin.add_argument("chat")
    p_dialog_unpin = dialog_sub.add_parser(
        "unpin", help="Unpin a dialog", parents=[global_flags]
    )
    p_dialog_unpin.add_argument("chat")

    p_api = sub.add_parser(
        "api", help="Call an allowlisted raw TL method", parents=[global_flags]
    )
    p_api.add_argument("method", metavar="METHOD")
    p_api.add_argument("--params", metavar="JSON")
    p_api.add_argument("--write", action="store_true")
    p_api.add_argument("--confirm", metavar="METHOD")

    p_export = sub.add_parser(
        "export", help="Export Telegram data", parents=[global_flags]
    )
    export_sub = p_export.add_subparsers(dest="export_kind", required=True)
    p_export_messages = export_sub.add_parser("messages", parents=[global_flags])
    p_export_messages.add_argument("chat", help="@username, t.me link, or dialog id")
    p_export_messages.add_argument("--output", required=True, type=Path)
    p_export_messages.add_argument("--limit", type=int)
    p_export_subscribers = export_sub.add_parser("subscribers", parents=[global_flags])
    p_export_subscribers.add_argument(
        "channel", help="@username, t.me link, or dialog id"
    )
    p_export_subscribers.add_argument("--output", required=True, type=Path)
    p_export_subscribers.add_argument("--limit", type=int)

    p_clone = sub.add_parser(
        "clone", help="Copy a supported chat", parents=[global_flags]
    )
    clone_sub = p_clone.add_subparsers(dest="clone_command", required=True)
    p_clone_status = clone_sub.add_parser("status", parents=[global_flags])
    p_clone_status.add_argument(
        "source", nargs="?", help="filter to one source (id or title substring)"
    )
    p_clone_init = clone_sub.add_parser("init", parents=[global_flags])
    p_clone_init.add_argument("source", help="source channel, supergroup, or dialog")
    p_clone_init.add_argument("--commit", metavar="PREVIEW_ID")
    p_clone_init.add_argument(
        "--replace",
        action="store_true",
        help="supersede an incompatible or stale clone: archive its state and "
        "start a fresh destination pair",
    )
    p_clone_sync = clone_sub.add_parser("sync", parents=[global_flags])
    p_clone_sync.add_argument("source", help="source channel, supergroup, or dialog")
    p_clone_sync.add_argument("--limit", type=int)

    return parser


async def _run_network(args, account) -> tuple[dict, list[tuple]]:
    mutation_safe = args.command == "clone" and (
        args.clone_command == "sync"
        or (args.clone_command == "init" and args.commit is not None)
    )
    try:
        async with session.client(account, mutation_safe=mutation_safe) as tg:
            if args.command == "dialogs":
                data = await dialogs_cmd.fetch_dialogs(
                    tg,
                    limit=args.limit,
                    unread_only=args.unread_only,
                    kind=args.kind,
                )
                return data, dialogs_cmd.to_rows(data)
            if args.command == "read":
                data = await read_cmd.fetch_messages(
                    tg,
                    args.chat,
                    limit=args.limit,
                    before_id=args.before_id,
                    after_id=args.after_id,
                    since=args.since,
                    until=args.until,
                    topic=args.topic,
                )
                return data, read_cmd.to_rows(data)
            if args.command == "search":
                if args.all:
                    data = await search_cmd.fetch_search_all(
                        tg, args.query, limit=args.limit
                    )
                else:
                    data = await search_cmd.fetch_search(
                        tg,
                        args.chat,
                        args.query,
                        limit=args.limit,
                        from_user=args.from_user,
                        since=args.since,
                    )
                return data, search_cmd.to_rows(data)
            if args.command == "latest":
                data = await search_cmd.fetch_latest(tg, args.chat)
                return data, search_cmd.to_rows(data)
            if args.command == "message":
                data = await read_cmd.fetch_message(
                    tg, args.chat, args.message_id, context=args.context
                )
                return data, search_cmd.to_rows(data)
            if args.command == "info":
                data = (
                    await info_cmd.fetch_info_full(tg, args.chat)
                    if args.full
                    else await info_cmd.fetch_info(tg, args.chat)
                )
                return data, info_cmd.to_rows(data)
            if args.command == "count":
                data = await info_cmd.fetch_count(tg, args.chat)
                return data, info_cmd.to_rows(data)
            if args.command == "resolve":
                data = await identity_cmd.resolve(tg, args.ref)
                return data, identity_cmd.to_rows(data)
            if args.command == "thread":
                data = await thread_cmd.fetch_thread(
                    tg,
                    args.chat,
                    args.message_id,
                    depth=args.depth,
                    want_replies=args.replies,
                    replies_limit=args.limit,
                )
                return data, thread_cmd.to_rows(data)
            if args.command == "contacts":
                if args.contacts_command == "list":
                    data = await identity_cmd.contacts_list(tg)
                else:
                    data = await identity_cmd.contacts_search(
                        tg, args.query, use_global=args.use_global
                    )
                return data, identity_cmd.contacts_to_rows(data)
            if args.command == "media" and args.media_command == "download":
                source = media_cmd.parse_source(args.source, args.message_id)

                def progress(current: int, total: int | None) -> None:
                    output.note(
                        f"downloaded {current}/{total if total is not None else '?'} bytes"
                    )

                data = await media_cmd.download_media(
                    tg,
                    source,
                    account.alias,
                    output=args.output,
                    parallel=args.parallel,
                    progress=progress,
                )
                return data, media_cmd.to_rows(data)
            if args.command == "media" and args.media_command == "manifest":
                data = await media_cmd.manifest(
                    tg,
                    args.source,
                    kind=args.media_type,
                    since=args.since,
                    limit=args.limit,
                )
                return data, media_cmd.manifest_to_rows(data)
            if args.command == "send":
                if args.preview:
                    data = await send_cmd.prepare(
                        tg,
                        args.chat,
                        args.text,
                        reply_to=args.reply_to,
                        file=args.file,
                        caption=args.caption,
                        topic=args.topic,
                        silent=args.silent,
                        fmt=args.format,
                    )
                else:
                    data = await send_cmd.commit(
                        tg,
                        args.commit,  # type: ignore  # preview load guards None
                        args.preview_payload,
                    )
                return data, send_cmd.to_rows(data)
            if args.command == "edit":
                if args.preview:
                    data = await mutate_cmd.prepare_edit(
                        tg, args.chat, args.message_id, args.text, args.format
                    )
                else:
                    data = await mutate_cmd.commit_edit(
                        tg,
                        args.commit,  # type: ignore  # preview load guards None
                        args.preview_payload,
                    )
                return data, mutate_cmd.to_rows(data)
            if args.command == "delete":
                if args.preview:
                    data = await mutate_cmd.prepare_delete(
                        tg, args.chat, args.message_id
                    )
                else:
                    data = await mutate_cmd.commit_delete(
                        tg,
                        args.commit,  # type: ignore  # preview load guards None
                        args.preview_payload,
                    )
                return data, mutate_cmd.to_rows(data)
            if args.command == "forward":
                if args.preview:
                    data = await mutate_cmd.prepare_forward(
                        tg, args.source, args.message_id, args.destination
                    )
                else:
                    data = await mutate_cmd.commit_forward(
                        tg,
                        args.commit,  # type: ignore  # preview load guards None
                        args.preview_payload,
                    )
                return data, mutate_cmd.to_rows(data)
            if args.command == "mark-read":
                data = await mutate_cmd.mark_read(tg, args.chat)
                return data, mutate_cmd.to_rows(data)
            if args.command == "mark-unread":
                data = await mutate_cmd.mark_unread(tg, args.chat)
                return data, mutate_cmd.to_rows(data)
            if args.command == "dialog":
                data = await dialog_cmd.set_pinned(
                    tg, args.chat, pinned=args.dialog_command == "pin"
                )
                return data, dialog_cmd.to_rows(data)
            if args.command == "api":
                return await api_cmd.call(tg, args.method, args.params), []
            if args.command == "export":
                if args.export_kind == "messages":
                    data = await export_cmd.export_messages(
                        tg, args.chat, args.output, limit=args.limit
                    )
                else:
                    data = await export_cmd.export_subscribers(
                        tg, args.channel, args.output, limit=args.limit
                    )
                return data, export_cmd.to_rows(data)
            if args.command == "clone" and args.clone_command == "init":
                if args.commit:
                    data = await clone_cmd.commit_init(
                        tg, args.source, account.alias, args.preview_payload
                    )
                else:
                    data = await clone_cmd.preview_init(
                        tg, args.source, replace=args.replace
                    )
                return data, clone_cmd.init_rows(data)
            if args.command == "clone" and args.clone_command == "sync":
                data = await clone_cmd.sync_text(
                    tg, args.source, account.alias, limit=args.limit
                )
                return data, clone_cmd.sync_rows(data)
            raise AssertionError(f"unhandled network command: {args.command}")
    except telethon_errors.TakeoutInitDelayError as exc:
        raise RateLimitError(
            f"takeout is unavailable for {exc.seconds}s; retry after {exc.seconds}s",
            retry_after=exc.seconds,
        ) from exc
    except telethon_errors.FloodWaitError as exc:
        raise RateLimitError(
            f"rate limited for {exc.seconds}s", retry_after=exc.seconds
        ) from exc


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as err:
        return 0 if err.code == 0 else 1
    timeout_supplied = hasattr(args, "timeout")
    no_default_timeout = args.command == "export" or (
        args.command == "clone" and args.clone_command == "sync"
    )
    for name, default in {
        "account": None,
        "json": False,
        "plain": False,
        "readonly": False,
        "timeout": None if no_default_timeout else 60.0,
        "verbose": False,
    }.items():
        if not hasattr(args, name):
            setattr(args, name, default)
    verbose_diagnostics = _enable_verbose_diagnostics() if args.verbose else []
    started = time.monotonic()
    exit_code = 1
    error_code = None
    try:
        if args.command == "search":
            if args.all:
                if args.query is not None or args.chat is None:
                    try:
                        parser.error("search --all takes exactly one QUERY")
                    except SystemExit:
                        return 1
                if args.from_user is not None or args.since is not None:
                    try:
                        parser.error("search --all only supports QUERY and --limit")
                    except SystemExit:
                        return 1
                args.query, args.chat = args.chat, None
            elif args.chat is None or args.query is None:
                try:
                    parser.error("search requires CHAT QUERY (or --all QUERY)")
                except SystemExit:
                    return 1
        if args.command in ("read", "search") or (
            args.command == "media" and args.media_command == "manifest"
        ):
            try:
                args.since = _parse_when(
                    parser, getattr(args, "since", None), "--since"
                )
                args.until = _parse_when(
                    parser, getattr(args, "until", None), "--until"
                )
            except SystemExit:
                return 1
        if args.command == "clone" and args.clone_command == "sync":
            safety.enforce_mutation_allowed(args.readonly)
            if args.limit is not None and args.limit <= 0:
                raise PolicyError("clone sync --limit must be positive")
        if args.command == "mark-read":
            safety.enforce_mutation_allowed(args.readonly)
        if args.command == "mark-unread":
            safety.enforce_mutation_allowed(args.readonly)
        if args.command == "dialog":
            safety.enforce_mutation_allowed(args.readonly)
        mutation_positionals = {
            "edit": ("chat", "message_id", "text"),
            "delete": ("chat", "message_id"),
            "forward": ("source", "message_id", "destination"),
        }
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
                    try:
                        parser.error("send --commit accepts only a preview id")
                    except SystemExit:
                        return 1
                safety.enforce_mutation_allowed(args.readonly)
                args.preview_payload = safety.begin_commit(
                    args.commit, expected_kind="send"
                )
            elif not (
                args.preview
                and args.chat is not None
                and (args.text is not None or args.file is not None)
            ):
                try:
                    parser.error(
                        "send requires CHAT (TEXT | --file PATH) --preview "
                        "or --commit PREVIEW_ID"
                    )
                except SystemExit:
                    return 1
        elif args.command in mutation_positionals:
            names = mutation_positionals[args.command]
            values = [getattr(args, name) for name in names]
            if args.commit:
                if args.preview or any(value is not None for value in values):
                    try:
                        parser.error(
                            f"{args.command} --commit accepts only a preview id"
                        )
                    except SystemExit:
                        return 1
                safety.enforce_mutation_allowed(args.readonly)
                args.preview_payload = safety.begin_commit(
                    args.commit, expected_kind=args.command
                )
            elif not (args.preview and all(value is not None for value in values)):
                try:
                    parser.error(
                        f"{args.command} requires "
                        f"{' '.join(name.upper() for name in names)} --preview "
                        "or --commit PREVIEW_ID"
                    )
                except SystemExit:
                    return 1
        if args.command == "clone" and args.clone_command == "init" and args.commit:
            safety.enforce_mutation_allowed(args.readonly)
            args.preview_payload = safety.consume_preview(args.commit)
            if (
                args.preview_payload.get("kind") != "clone-init"
                or args.preview_payload.get("source") != args.source
            ):
                raise PolicyError("clone init preview does not match this source")
        if args.command == "api" and args.write:
            safety.enforce_mutation_allowed(args.readonly)
            args.method = api_cmd.canonical_method(args.method)
            if api_cmd.is_hard_denied(args.method):
                raise PolicyError("raw API method is permanently denied")
            confirm = (
                api_cmd.try_canonical_method(args.confirm)
                if args.confirm
                else args.confirm
            )
            if api_cmd.requires_confirmation(args.method) and confirm != args.method:
                raise PolicyError(
                    "raw API destructive write requires exact --confirm METHOD"
                )
        if args.command == "api" and not args.write:
            canonical = api_cmd.try_canonical_method(args.method)
            if canonical is None or not api_cmd.is_read_method(canonical):
                raise PolicyError("raw API method is not allowlisted for read-only use")
            args.method = canonical
        if args.command == "api" and args.params is None:
            try:
                parser.error("the following arguments are required: --params")
            except SystemExit:
                return 1
        if args.command == "accounts" and args.subcommand == "import":
            data = accounts_cmd.import_accounts(
                args.aliases or None, args.source_root.expanduser(), force=args.force
            )
            rows = accounts_cmd.import_rows(data)
        elif args.command == "clone" and args.clone_command == "status":
            data = clone_cmd.list_clones(args.source)
            rows = clone_cmd.status_rows(data)
        else:
            config = load_config()
            if args.command == "accounts":
                data = accounts_cmd.list_accounts(config)
                rows = accounts_cmd.to_rows(data)
            elif args.command == "doctor":
                data = asyncio.run(
                    asyncio.wait_for(
                        doctor_cmd.run(config, args.account), timeout=args.timeout
                    )
                )
                rows = doctor_cmd.to_rows(data)
            else:
                account = resolve_account(config, args.account)
                args.account = account.alias
                if args.verbose:
                    LOGGER.debug(
                        "resolved account=%s command=%s", account.alias, args.command
                    )
                if args.command in ("send", "edit", "delete", "forward") and getattr(
                    args, "commit", None
                ):
                    details = {"preview_id": args.commit}
                    if "random_id" in args.preview_payload:
                        details["random_id"] = args.preview_payload["random_id"]
                    safety.append_audit(
                        args.command,
                        account.alias,
                        details,
                    )
                if args.command == "api" and args.write:
                    safety.append_audit("api", account.alias, {"method": args.method})
                if args.command == "mark-read":
                    safety.append_audit("mark-read", account.alias, {"chat": args.chat})
                if args.command == "mark-unread":
                    safety.append_audit(
                        "mark-unread", account.alias, {"chat": args.chat}
                    )
                if args.command == "dialog":
                    safety.append_audit(
                        f"dialog-{args.dialog_command}",
                        account.alias,
                        {"chat": args.chat},
                    )
                network = _run_network(args, account)
                if (
                    args.command == "media"
                    or (args.command == "clone" and args.clone_command == "sync")
                ) and not timeout_supplied:
                    data, rows = asyncio.run(network)
                else:
                    data, rows = asyncio.run(
                        asyncio.wait_for(network, timeout=args.timeout)
                    )
                if args.command in ("send", "edit", "delete", "forward") and getattr(
                    args, "commit", None
                ):
                    safety.append_audit(
                        f"{args.command}-result",
                        account.alias,
                        {
                            "preview_id": args.commit,
                            "message_id": data.get("message_id"),
                        },
                    )
                    safety.finish_commit(args.commit)
    except TgcliError as err:
        output.emit_error(err, as_json=args.json)
        error_code = err.code
        exit_code = err.exit_code
    except Exception:
        error_code = "UNHANDLED"
        raise
    else:
        if args.json:
            output.emit_json(data)
        elif args.plain:
            output.emit_plain(rows)
        else:
            output.emit_plain(
                [
                    (" | ".join("" if cell is None else str(cell) for cell in row),)
                    for row in rows
                ]
            )
        exit_code = 0
    finally:
        duration_ms = int((time.monotonic() - started) * 1000)
        if args.verbose:
            LOGGER.debug(
                "completed command=%s exit_code=%s duration_ms=%s",
                args.command,
                exit_code,
                duration_ms,
            )
        invocations.log_invocation(
            command=args.command,
            account=args.account,
            exit_code=exit_code,
            error=error_code,
            duration_ms=duration_ms,
        )
        _restore_diagnostics(verbose_diagnostics)
    return exit_code


def entrypoint() -> None:
    sys.exit(main())
