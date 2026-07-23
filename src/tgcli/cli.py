"""Process lifecycle for `tg`: parse, preflight, execute, emit, journal.

The grammar lives in parser.py, pre-network validation in preflight.py, and
network routing in dispatch.py. What stays here is the shape of a single
invocation and nothing else.
"""

import asyncio
import logging
import sys
import time

from tgcli import dispatch, invocations, output, preflight, safety
from tgcli.commands import accounts as accounts_cmd
from tgcli.commands import clone as clone_cmd
from tgcli.commands import doctor as doctor_cmd
from tgcli.commands import store as store_cmd
from tgcli.config import load_config, resolve_account
from tgcli.errors import PartialFailure, TgcliError
from tgcli.parser import build_parser
from tgcli import session


LOGGER = logging.getLogger(__name__)

__all__ = ["build_parser", "main", "entrypoint"]


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


def _apply_global_defaults(args) -> None:
    """Backfill global flags argparse suppressed on the subparser it matched."""
    no_default_timeout = args.command == "export" or (
        args.command == "clone" and args.clone_command == "sync"
    )
    defaults = {
        "account": None,
        "json": False,
        "plain": False,
        "readonly": False,
        "timeout": None if no_default_timeout else 60.0,
        "verbose": False,
    }
    for name, default in defaults.items():
        if not hasattr(args, name):
            setattr(args, name, default)


async def _run_network(args, account) -> tuple[dict, list[tuple]]:
    return await dispatch.run_network(args, account)


def _audit_before(args, account) -> None:
    if args.command in ("send", "edit", "delete", "forward") and getattr(
        args, "commit", None
    ):
        details = {"preview_id": args.commit}
        if "random_id" in args.preview_payload:
            details["random_id"] = args.preview_payload["random_id"]
        safety.append_audit(args.command, account.alias, details)
    if (
        args.command == "draft"
        and args.draft_command in ("set", "clear")
        and getattr(args, "commit", None)
    ):
        safety.append_audit(
            f"draft-{args.draft_command}",
            account.alias,
            {"preview_id": args.commit, "chat": args.preview_payload.get("chat")},
        )
    if args.command == "api" and args.write:
        safety.append_audit("api", account.alias, {"method": args.method})
    if args.command in ("mark-read", "mark-unread"):
        safety.append_audit(args.command, account.alias, {"chat": args.chat})
    if args.command == "dialog":
        safety.append_audit(
            f"dialog-{args.dialog_command}", account.alias, {"chat": args.chat}
        )


def _audit_after(args, account, data) -> None:
    if args.command in ("send", "edit", "delete", "forward") and getattr(
        args, "commit", None
    ):
        safety.append_audit(
            f"{args.command}-result",
            account.alias,
            {"preview_id": args.commit, "message_id": data.get("message_id")},
        )
        safety.finish_commit(args.commit)
    if (
        args.command == "draft"
        and args.draft_command in ("set", "clear")
        and getattr(args, "commit", None)
    ):
        safety.append_audit(
            f"draft-{args.draft_command}-result",
            account.alias,
            {"preview_id": args.commit, "chat": data.get("draft", {}).get("chat")},
        )
        safety.finish_commit(args.commit)


def _execute(args, *, timeout_supplied: bool) -> tuple[dict, list[tuple]]:
    """Run one prepared invocation, opening only the resources it needs."""
    if args.command == "accounts" and args.subcommand == "import":
        data = accounts_cmd.import_accounts(
            args.aliases or None, args.source_root.expanduser(), force=args.force
        )
        return data, accounts_cmd.import_rows(data)
    if args.command == "clone" and args.clone_command == "status":
        data = clone_cmd.list_clones(args.source)
        return data, clone_cmd.status_rows(data)
    if args.command == "store" and args.store_command == "stats":
        data = store_cmd.stats(session.state_dir())
        return data, store_cmd.stats_rows(data)

    config = load_config()
    if args.command == "accounts":
        data = accounts_cmd.list_accounts(config)
        return data, accounts_cmd.to_rows(data)
    if args.command == "doctor":
        data = asyncio.run(
            asyncio.wait_for(doctor_cmd.run(config, args.account), timeout=args.timeout)
        )
        return data, doctor_cmd.to_rows(data)

    account = resolve_account(config, args.account)
    args.account = account.alias
    if args.verbose:
        LOGGER.debug("resolved account=%s command=%s", account.alias, args.command)
    _audit_before(args, account)
    network = _run_network(args, account)
    long_running = args.command == "media" or (
        args.command == "clone" and args.clone_command == "sync"
    )
    if long_running and not timeout_supplied:
        data, rows = asyncio.run(network)
    else:
        data, rows = asyncio.run(asyncio.wait_for(network, timeout=args.timeout))
    _audit_after(args, account, data)
    return data, rows


def _emit(args, data, rows) -> None:
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


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as err:
        return 0 if err.code == 0 else 1
    timeout_supplied = hasattr(args, "timeout")
    _apply_global_defaults(args)
    verbose_diagnostics = _enable_verbose_diagnostics() if args.verbose else []
    started = time.monotonic()
    exit_code = 1
    error_code = None
    try:
        preflight.prepare(parser, args)
        data, rows = _execute(args, timeout_supplied=timeout_supplied)
    except SystemExit:
        # parser.error() already wrote usage to stderr.
        exit_code = 1
    except PartialFailure as err:
        if args.json:
            output.emit_json(err.data)
        elif args.plain:
            output.emit_plain(
                err.rows if err.rows is not None else err.data.get("rows") or []
            )
        else:
            output.emit_error(err, as_json=False)
        error_code = err.code
        exit_code = err.exit_code
    except TgcliError as err:
        output.emit_error(err, as_json=args.json)
        error_code = err.code
        exit_code = err.exit_code
    except Exception:
        error_code = "UNHANDLED"
        raise
    else:
        if args.command == "batch":
            output.emit_json_lines(data["_batch_results"])
            exit_code = data["_batch_exit"] or 0
        else:
            _emit(args, data, rows)
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
