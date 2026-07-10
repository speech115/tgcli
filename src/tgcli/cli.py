import argparse
import asyncio
import sys

from telethon import errors as telethon_errors

from tgcli import __version__, output, session
from tgcli.commands import accounts as accounts_cmd
from tgcli.commands import dialogs as dialogs_cmd
from tgcli.commands import info as info_cmd
from tgcli.commands import read as read_cmd
from tgcli.commands import search as search_cmd
from tgcli.config import load_config, resolve_account
from tgcli.errors import RateLimitError, TgcliError


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
            raise AssertionError(f"unhandled network command: {args.command}")
    except telethon_errors.FloodWaitError as exc:
        raise RateLimitError(
            f"rate limited for {exc.seconds}s", retry_after=exc.seconds
        ) from exc


def main(argv: list[str] | None = None) -> int:
    try:
        args = build_parser().parse_args(argv)
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
        config = load_config()
        if args.command == "accounts":
            data = accounts_cmd.list_accounts(config)
            rows = accounts_cmd.to_rows(data)
        else:
            account = resolve_account(config, args.account)
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
