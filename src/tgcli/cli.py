"""Process lifecycle for `tg`: parse, preflight, execute, emit, journal.

The grammar lives in parser.py, pre-network validation in preflight.py, and
network routing in dispatch.py. What stays here is the shape of a single
invocation and nothing else.
"""

import asyncio
import logging
import os
import sys
import time
import traceback
from contextlib import contextmanager

from tgcli import dispatch, invocations, output, preflight, safety
from tgcli.commands import accounts as accounts_cmd
from tgcli.commands import clone as clone_cmd
from tgcli.commands import doctor as doctor_cmd
from tgcli.commands import login as login_cmd
from tgcli.commands import store as store_cmd
from tgcli.config import load_config, resolve_account
from tgcli.errors import CommandTimeoutError, PartialFailure, TgcliError
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
    no_default_timeout = (
        args.command == "export"
        or (args.command == "clone" and args.clone_command == "sync")
        or (
            args.command == "accounts"
            and args.subcommand == "login"
            and getattr(args, "continue_id", None)
        )
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


def _run_with_deadline(coro, timeout):
    """Run one coroutine under `--timeout` as the documented TIMEOUT error."""
    try:
        return asyncio.run(asyncio.wait_for(coro, timeout=timeout))
    except TimeoutError:  # asyncio.TimeoutError is this alias since 3.11
        raise CommandTimeoutError(
            f"invocation exceeded the --timeout deadline of {timeout}s"
        ) from None


@contextmanager
def _tolerate_hangup():
    """Let an error envelope fail to reach a reader that already hung up.

    The error arms are siblings of `except BrokenPipeError`, so without this
    a closed pipe would escape past the journal and leave the run recorded
    with its pre-failure codes.
    """
    try:
        yield
    except BrokenPipeError:
        _silence_stdout()


def _silence_stdout() -> None:
    """Point stdout at /dev/null so shutdown cannot re-raise a broken pipe."""
    try:
        fd = sys.stdout.fileno()
    except (OSError, ValueError):
        return
    devnull = os.open(os.devnull, os.O_WRONLY)
    os.dup2(devnull, fd)
    os.close(devnull)


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
    if args.command == "store" and args.store_command == "cleanup":
        if args.confirm:
            safety.enforce_local_mutation_allowed(args.readonly)
        data = store_cmd.cleanup(
            session.state_dir(),
            older_than=getattr(args, "older_than", None),
            include_pending=bool(getattr(args, "include_pending", False)),
            confirm=bool(args.confirm),
        )
        return data, store_cmd.cleanup_rows(data)

    config = load_config()
    if args.command == "accounts" and args.subcommand == "show":
        data = accounts_cmd.show_account(config, args.alias)
        return data, accounts_cmd.show_rows(data)
    if args.command == "accounts" and args.subcommand == "remove":
        if args.confirm:
            safety.enforce_local_mutation_allowed(args.readonly)
        data = accounts_cmd.remove_account(
            config,
            args.alias,
            confirm=bool(args.confirm),
            keep_session=bool(getattr(args, "keep_session", False)),
        )
        return data, accounts_cmd.remove_rows(data)
    if args.command == "accounts" and args.subcommand == "login":
        timeout = args.timeout if timeout_supplied else 120.0
        if getattr(args, "continue_id", None):
            data = asyncio.run(
                login_cmd.continue_login(
                    login_id=args.continue_id,
                    code=getattr(args, "code", None),
                    password_stdin=bool(getattr(args, "password_stdin", False)),
                )
            )
        else:
            data = asyncio.run(
                login_cmd.start_login(
                    config,
                    args.alias,
                    phone=getattr(args, "phone", None),
                    api_id=getattr(args, "api_id", None),
                    api_hash=getattr(args, "api_hash", None),
                    force=bool(getattr(args, "force", False)),
                    timeout=timeout,
                    qr_format=getattr(args, "qr_format", "link"),
                    password_stdin=bool(getattr(args, "password_stdin", False)),
                )
            )
        return data, login_cmd.login_rows(data)
    if args.command == "accounts":
        data = accounts_cmd.list_accounts(config)
        return data, accounts_cmd.to_rows(data)
    if args.command == "doctor":
        connect = bool(getattr(args, "connect", False))
        coro = doctor_cmd.run(config, args.account, connect=connect)
        if connect:
            data = _run_with_deadline(coro, args.timeout)
        else:
            data = asyncio.run(coro)
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
        data, rows = _run_with_deadline(network, args.timeout)
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
        # Emitting is part of the invocation: a failure here is journaled,
        # not reported as a success.
        if args.command == "batch":
            output.emit_json_lines(data["_batch_results"])
            exit_code = data["_batch_exit"] or 0
        else:
            _emit(args, data, rows)
            exit_code = 0
    except SystemExit:
        # parser.error() already wrote usage to stderr.
        exit_code = 1
    except BrokenPipeError:
        # The reader hung up (`| head`): stop writing and leave quietly.
        _silence_stdout()
        error_code = "BROKEN_PIPE"
        exit_code = 0
    except PartialFailure as err:
        # Codes are recorded before the write: a reader that hangs up mid-
        # envelope must not replace the real failure in the journal.
        error_code = err.code
        exit_code = err.exit_code
        with _tolerate_hangup():
            if args.json:
                output.emit_json(err.data)
            elif args.plain:
                output.emit_plain(
                    err.rows if err.rows is not None else err.data.get("rows") or []
                )
            else:
                output.emit_error(err, as_json=False)
    except TgcliError as err:
        error_code = err.code
        exit_code = err.exit_code
        with _tolerate_hangup():
            output.emit_error(err, as_json=args.json)
    except Exception as err:
        # Untranslated failure (network, RPC, bug): still one envelope, and a
        # traceback only when the caller asked for diagnostics.
        error_code = "RUNTIME"
        exit_code = 1
        if args.verbose:
            traceback.print_exc()
        with _tolerate_hangup():
            output.emit_error(
                TgcliError(str(err) or type(err).__name__), as_json=args.json
            )
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
    try:
        exit_code = main()
    except BrokenPipeError:
        _silence_stdout()
        exit_code = 0
    sys.exit(exit_code)
