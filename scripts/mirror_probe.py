#!/usr/bin/env python3
"""Read-only Telegram capability probe; performs no Telegram mutation."""

import argparse
import asyncio
import json
import sys
from pathlib import Path

from tgcli import config, session
from tgcli.errors import ConfigError, NotFoundError
from tgcli.mirror_probe import probe_chat, write_report


async def run(args) -> dict:
    account = config.resolve_account(config.load_config(), args.account)
    async with session.client(account) as tg:
        me = await tg.get_me()
        return await probe_chat(
            tg,
            args.chat,
            me.id,
            role=args.role,
            limit=args.limit,
            samples_per_kind=args.samples_per_kind,
        )


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("chat")
    parser.add_argument("--account", default=None)
    parser.add_argument(
        "--role", required=True, choices=("owned", "subscriber", "lab")
    )
    parser.add_argument("--limit", type=int, default=1000)
    parser.add_argument("--samples-per-kind", type=int, default=3)
    parser.add_argument("--output", required=True)
    args = parser.parse_args(argv)
    if args.limit < 1:
        parser.error("--limit must be positive")
    if args.samples_per_kind < 1:
        parser.error("--samples-per-kind must be positive")
    return args


def main(argv=None) -> int:
    args = parse_args(argv)
    try:
        report = asyncio.run(run(args))
        write_report(Path(args.output), report)
        print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        return 0
    except ConfigError as exc:
        print(f"probe: {exc}", file=sys.stderr)
        return 3
    except (NotFoundError, ValueError):
        print("probe: chat not found or invalid", file=sys.stderr)
        return 4
    except Exception as exc:
        print(f"probe: {type(exc).__name__}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
