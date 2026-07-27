"""Process lifecycle for `tg`: parse, preflight, execute, emit, journal.

The grammar lives in parser.py, pre-network validation in preflight.py, and
network routing in dispatch.py. What stays here is the shape of a single
invocation and nothing else.
"""

import argparse
import asyncio
import contextlib
import io
import logging
import os
import signal
import sys
import threading
import time
import traceback
from contextlib import contextmanager

from tgcli import dispatch, invocations, output, preflight, safety, session
from tgcli.commands import (
    accounts as accounts_cmd,
    api as api_cmd,
    clone as clone_cmd,
    doctor as doctor_cmd,
    login as login_cmd,
    store as store_cmd,
)
from tgcli.config import load_config, resolve_account
from tgcli.errors import CommandTimeoutError, PartialFailure, TgcliError
from tgcli.parser import build_parser

LOGGER = logging.getLogger(__name__)

__all__ = ["build_parser", "main", "entrypoint"]

# The deadline is armed before preflight, so it must not preempt a command that
# owns a graceful deadline of its own (the QR wait, asyncio.wait_for around the
# network). Those arm later; this margin keeps them first.
DEADLINE_GRACE = 1.0

# The only argv tokens that may end a run successfully with text on stdout.
# argparse groups single-dash short options, so the value `-hi` also reaches
# the help action — that exit is misuse, not a help request.
HELP_TOKENS = frozenset({"-h", "--help", "--version"})

# Catchable termination signals; SIGKILL cannot be one and is left alone.
TERMINATION_SIGNALS = (signal.SIGTERM, signal.SIGHUP)


class _DeadlineSignal(BaseException):
    """SIGALRM escape: a BaseException so no `except Exception` swallows it."""


class _TerminationSignal(BaseException):
    """SIGTERM/SIGHUP escape: a BaseException, for the same reason."""

    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


class UsageError(TgcliError):
    """Argument misuse; parser.error already put the detail on stderr."""

    code = "USAGE"


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


def _default_timeout(args) -> float | None:
    """The deadline for an invocation that supplied no --timeout (CONTRACT §1)."""
    if args.command == "export":
        return None
    if args.command == "changes" and getattr(args, "changes_wait", None) is not None:
        # --wait owns the budget; an implicit 60s must not clip it.
        return None
    if args.command == "clone" and args.clone_command in ("init", "sync", "refresh"):
        # ADR-0052 lets these wait out a short FloodWait (up to 61s in the
        # foreground), which never fits inside a 60s default deadline.
        return None
    if args.command == "accounts" and args.subcommand == "login":
        # CONTRACT §10: the QR wait defaults to 120s; --continue waits on the
        # operator and takes no default deadline at all.
        return None if getattr(args, "continue_id", None) else 120.0
    return 60.0


def _apply_global_defaults(args) -> None:
    """Backfill global flags argparse suppressed on the subparser it matched."""
    defaults = {
        "account": None,
        "json": False,
        "plain": False,
        "readonly": False,
        "timeout": _default_timeout(args),
        "verbose": False,
    }
    for name, default in defaults.items():
        if not hasattr(args, name):
            setattr(args, name, default)


def _long_running(args) -> bool:
    """Commands that pace themselves rather than honour a default deadline."""
    return args.command == "media" or (
        args.command == "clone" and args.clone_command == "sync"
    )


def _deadline(args, *, timeout_supplied: bool) -> float | None:
    """The budget for preflight plus execute together, or None when exempt."""
    if args.timeout is None or (not timeout_supplied and _long_running(args)):
        return None
    return args.timeout


@contextlib.contextmanager
def _armed(seconds: float | None):
    """Hold the invocation deadline over the whole body, preflight included.

    asyncio.wait_for only covers the network coroutine; everything before it —
    `tg batch` reading stdin, `accounts login --continue` reading a password —
    would otherwise run with no deadline at all.
    """
    if (
        seconds is None
        or seconds <= 0
        or threading.current_thread() is not threading.main_thread()
    ):
        yield
        return

    def fire(signum, frame):
        raise _DeadlineSignal

    previous = signal.signal(signal.SIGALRM, fire)
    signal.setitimer(signal.ITIMER_REAL, seconds + DEADLINE_GRACE)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


@contextlib.contextmanager
def _honest_termination():
    """Let SIGTERM/SIGHUP journal the run, then die by the signal anyway.

    CONTRACT §9 promises one object per parsed command; a default-disposition
    kill appends none. Raising from the handler lets main's `finally` write the
    honest row, and `entrypoint` then restores the default disposition and
    re-raises so the shell still sees a signal death. Nothing else is done
    here: a killed run must not wait on cleanup that could hang.
    """
    if threading.current_thread() is not threading.main_thread():
        yield
        return

    def fire(signum, frame):
        raise _TerminationSignal(signum)

    previous = [(number, signal.signal(number, fire)) for number in TERMINATION_SIGNALS]
    try:
        yield
    finally:
        for number, handler in previous:
            signal.signal(number, handler)


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
        # audit_details adds what the write touched (ADR-0010/0011): a record
        # naming only the method cannot answer the one question an audit log
        # exists for.
        safety.append_audit(
            "api", account.alias, api_cmd.audit_details(args.method, args.params)
        )
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
            role=getattr(args, "remove_role", None),
        )
        return data, accounts_cmd.remove_rows(data)
    if args.command == "accounts" and args.subcommand == "login":
        timeout = args.timeout
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
                    role=getattr(args, "login_role", None),
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
    if _long_running(args) and not timeout_supplied:
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


def _parse(
    parser: argparse.ArgumentParser, argv: list[str] | None
) -> argparse.Namespace | int:
    """Parse argv, or return the exit code of a run that never got a command.

    argparse prints help on stdout and exits 0 for `-h`, and it reaches `-h`
    from any grouped short option: the value `-hi` would otherwise be a silent
    no-op reported as success. Held-back help text reaches stdout only when an
    exact help or version token proves the caller asked for it.
    """
    tokens = sys.argv[1:] if argv is None else argv
    printed = io.StringIO()
    try:
        with contextlib.redirect_stdout(printed):
            return parser.parse_args(tokens)
    except SystemExit as err:
        if err.code == 0 and HELP_TOKENS.intersection(tokens):
            sys.stdout.write(printed.getvalue())
            return 0
        if err.code == 0:
            with contextlib.suppress(SystemExit):
                parser.error(
                    "a value starting with '-' was read as options; pass the "
                    "flags first and such values after '--'"
                )
        return 1


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    parsed = _parse(parser, argv)
    if isinstance(parsed, int):
        return parsed
    args = parsed
    timeout_supplied = hasattr(args, "timeout")
    _apply_global_defaults(args)
    verbose_diagnostics = _enable_verbose_diagnostics() if args.verbose else []
    started = time.monotonic()
    exit_code = 1
    error_code = None
    try:
        with _honest_termination():
            with _armed(_deadline(args, timeout_supplied=timeout_supplied)):
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
        # parser.error() already wrote usage to stderr, but a --json caller is
        # still owed exactly one document on stdout (CONTRACT §2).
        error_code = "USAGE"
        exit_code = 1
        if args.json:
            with _tolerate_hangup():
                output.emit_error(UsageError("invalid arguments"), as_json=True)
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
    except _DeadlineSignal:
        # The whole-body alarm fired; asyncio.wait_for's own TimeoutError is
        # already translated by _run_with_deadline.
        timed_out = CommandTimeoutError("invocation deadline exceeded")
        error_code = timed_out.code
        exit_code = timed_out.exit_code
        with _tolerate_hangup():
            output.emit_error(timed_out, as_json=args.json)
    except _TerminationSignal as err:
        # The row is owed before the process leaves; entrypoint turns this
        # back into the signal death the caller asked for.
        error_code = "TERMINATED"
        exit_code = 128 + err.signum
        raise
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
    except BaseException as err:
        # An abnormal unwind (SIGINT) must not leave the journal claiming the
        # pre-failure exit code with no error at all.
        interrupted = isinstance(err, KeyboardInterrupt)
        error_code = "INTERRUPTED" if interrupted else "UNHANDLED"
        exit_code = 130 if interrupted else 1
        raise
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
            role=getattr(args, "session_role", None),
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
    except _TerminationSignal as err:
        # The journal row is written; leave the way the signal asked.
        signal.signal(err.signum, signal.SIG_DFL)
        os.kill(os.getpid(), err.signum)
        exit_code = 128 + err.signum  # unreachable: the signal lands first
    sys.exit(exit_code)
