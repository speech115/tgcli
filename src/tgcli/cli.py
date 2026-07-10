import argparse
import asyncio
import sys

from telethon import errors as telethon_errors

from tgcli import __version__, output, safety, session
from tgcli.commands import accounts as accounts_cmd
from tgcli.commands import api as api_cmd
from tgcli.commands import dialogs as dialogs_cmd
from tgcli.commands import info as info_cmd
from tgcli.commands import read as read_cmd
from tgcli.commands import search as search_cmd
from tgcli.commands import send as send_cmd
from tgcli.config import load_config, resolve_account
from tgcli.errors import PolicyError, RateLimitError, TgcliError


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

    p_dialogs = sub.add_parser("dialogs", help="List dialogs", parents=[global_flags])
    p_dialogs.add_argument("--limit", type=int, default=50)

    p_read = sub.add_parser("read", help="Read recent messages from a dialog", parents=[global_flags])
    p_read.add_argument("chat", help="@username, t.me link, or dialog id")
    p_read.add_argument("--limit", type=int, default=20)

    p_search = sub.add_parser("search", help="Search messages in a dialog", parents=[global_flags])
    p_search.add_argument("chat", help="@username, t.me link, or dialog id")
    p_search.add_argument("query")
    p_search.add_argument("--limit", type=int, default=20)

    p_latest = sub.add_parser("latest", help="Read the latest dialog message", parents=[global_flags])
    p_latest.add_argument("chat", help="@username, t.me link, or dialog id")

    p_message = sub.add_parser("message", help="Read one message by id", parents=[global_flags])
    p_message.add_argument("chat", help="@username, t.me link, or dialog id")
    p_message.add_argument("message_id", type=int)

    p_info = sub.add_parser("info", help="Show dialog metadata", parents=[global_flags])
    p_info.add_argument("chat", help="@username, t.me link, or dialog id")

    p_count = sub.add_parser("count", help="Count dialog messages", parents=[global_flags])
    p_count.add_argument("chat", help="@username, t.me link, or dialog id")

    p_send = sub.add_parser("send", help="Preview and commit a message", parents=[global_flags])
    p_send.add_argument("chat", nargs="?", help="target for --preview")
    p_send.add_argument("text", nargs="?", help="message text for --preview")
    p_send.add_argument("--preview", action="store_true")
    p_send.add_argument("--commit", metavar="PREVIEW_ID")

    p_api = sub.add_parser("api", help="Call an allowlisted raw TL method", parents=[global_flags])
    p_api.add_argument("method", metavar="METHOD")
    p_api.add_argument("--params", metavar="JSON")
    p_api.add_argument("--write", action="store_true")
    p_api.add_argument("--confirm", metavar="METHOD")

    return parser


async def _run_network(args, account) -> tuple[dict, list[tuple]]:
    try:
        async with session.client(account) as tg:
            if args.command == "dialogs":
                data = await dialogs_cmd.fetch_dialogs(tg, limit=args.limit)
                return data, dialogs_cmd.to_rows(data)
            if args.command == "read":
                data = await read_cmd.fetch_messages(tg, args.chat, limit=args.limit)
                return data, read_cmd.to_rows(data)
            if args.command == "search":
                data = await search_cmd.fetch_search(
                    tg, args.chat, args.query, limit=args.limit
                )
                return data, search_cmd.to_rows(data)
            if args.command == "latest":
                data = await search_cmd.fetch_latest(tg, args.chat)
                return data, search_cmd.to_rows(data)
            if args.command == "message":
                data = await read_cmd.fetch_message(tg, args.chat, args.message_id)
                return data, search_cmd.to_rows(data)
            if args.command == "info":
                data = await info_cmd.fetch_info(tg, args.chat)
                return data, info_cmd.to_rows(data)
            if args.command == "count":
                data = await info_cmd.fetch_count(tg, args.chat)
                return data, info_cmd.to_rows(data)
            if args.command == "send":
                if args.preview:
                    data = await send_cmd.prepare(tg, args.chat, args.text)
                else:
                    data = await send_cmd.commit(
                        tg, args.commit, args.preview_payload
                    )
                return data, send_cmd.to_rows(data)
            if args.command == "api":
                return await api_cmd.call(tg, args.method, args.params), []
            raise AssertionError(f"unhandled network command: {args.command}")
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
    for name, default in {
        "account": None,
        "json": False,
        "plain": False,
        "readonly": False,
        "timeout": 60.0,
        "verbose": False,
    }.items():
        if not hasattr(args, name):
            setattr(args, name, default)
    try:
        if args.command == "send":
            if args.commit:
                if args.preview or args.chat is not None or args.text is not None:
                    try:
                        parser.error("send --commit accepts only a preview id")
                    except SystemExit:
                        return 1
                safety.enforce_mutation_allowed(args.readonly)
                args.preview_payload = safety.consume_preview(args.commit)
            elif not (args.preview and args.chat is not None and args.text is not None):
                try:
                    parser.error("send requires CHAT TEXT --preview or --commit PREVIEW_ID")
                except SystemExit:
                    return 1
        if args.command == "api" and args.write:
            safety.enforce_mutation_allowed(args.readonly)
            args.method = api_cmd.canonical_method(args.method)
            if api_cmd.is_hard_denied(args.method):
                raise PolicyError("raw API method is permanently denied")
            confirm = api_cmd.try_canonical_method(args.confirm) if args.confirm else args.confirm
            if api_cmd.requires_confirmation(args.method) and confirm != args.method:
                raise PolicyError("raw API destructive write requires exact --confirm METHOD")
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
        config = load_config()
        if args.command == "accounts":
            data = accounts_cmd.list_accounts(config)
            rows = accounts_cmd.to_rows(data)
        else:
            account = resolve_account(config, args.account)
            if args.command == "send" and args.commit:
                safety.append_audit("send", account.alias, {"preview_id": args.commit})
            if args.command == "api" and args.write:
                safety.append_audit("api", account.alias, {"method": args.method})
            data, rows = asyncio.run(
                asyncio.wait_for(_run_network(args, account), timeout=args.timeout)
            )
    except TgcliError as err:
        output.emit_error(err, as_json=args.json)
        return err.exit_code
    if args.json:
        output.emit_json(data)
    elif args.plain:
        output.emit_plain(rows)
    else:
        output.emit_plain(
            [(" | ".join("" if cell is None else str(cell) for cell in row),) for row in rows]
        )
    return 0


def entrypoint() -> None:
    sys.exit(main())
