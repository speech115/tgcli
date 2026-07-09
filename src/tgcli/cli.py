import argparse
import asyncio
import sys

from tgcli import __version__, output
from tgcli.commands import accounts as accounts_cmd
from tgcli.config import load_config, resolve_account
from tgcli.errors import TgcliError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="tg", description="Stateless Telegram CLI")
    parser.add_argument("--version", action="version", version=__version__)
    parser.add_argument("--account", help="account alias from config")
    parser.add_argument("--json", action="store_true", help="JSON to stdout")
    parser.add_argument("--plain", action="store_true", help="TSV to stdout")
    parser.add_argument("--timeout", type=float, default=60.0)
    sub = parser.add_subparsers(dest="command", required=True)

    p_accounts = sub.add_parser("accounts", help="Manage accounts")
    accounts_sub = p_accounts.add_subparsers(dest="subcommand", required=True)
    accounts_sub.add_parser("list", help="List configured accounts")

    return parser


async def _run_network(args, account) -> tuple[dict, list[tuple]]:
    raise AssertionError(f"unhandled network command: {args.command}")


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
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
    else:
        output.emit_plain(rows)
    return 0


def entrypoint() -> None:
    sys.exit(main())
