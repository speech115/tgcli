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
import math
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
    archive as archive_cmd,
    clone as clone_cmd,
    doctor as doctor_cmd,
    jobs as jobs_cmd,
    login as login_cmd,
    store as store_cmd,
)
from tgcli.config import load_config, resolve_account
from tgcli.errors import CommandTimeoutError, PartialFailure, TgcliError
from tgcli.jobs import preflight as jobs_preflight
from tgcli.parser import build_parser

LOGGER = logging.getLogger(__name__)

__all__ = ["build_parser", "main", "entrypoint"]

# The deadline is armed before preflight, so it must not preempt a command that
# owns a graceful asyncio deadline around the network. Those arm later; this
# margin keeps them first.
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
    """The default deadline (CONTRACT §1): 60 s, a hang detector.

    Governed sleep never counts against it (ADR-0072 decision 6), and
    long-running commands keep no implicit deadline — CONTRACT §1 lists
    them; only explicit `--timeout`/`--max-runtime` bounds them.
    """
    if (
        args.command == "accounts"
        and args.subcommand == "login"
        and getattr(args, "continue_id", None)
    ):
        return None
    if args.command == "transcribe":
        return 120.0
    if args.command == "changes" and getattr(args, "changes_wait", None) is not None:
        return None
    if _long_running_command(args):
        return None
    return 60.0


def _long_running_command(args) -> bool:
    """Commands CONTRACT §1 exempts from the implicit 60 s deadline."""
    return (
        args.command == "media"
        or args.command == "export"
        or (
            args.command == "clone"
            and args.clone_command in ("init", "sync", "refresh")
        )
        or (args.command == "jobs" and args.jobs_command == "run")
    )


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


@contextlib.contextmanager
def _armed(seconds: float | None):
    """Hold the invocation deadline over the whole body, preflight included.

    asyncio.wait_for covers only the network coroutine; stdin reads etc.
    would otherwise run with no deadline at all.

    The deadline is a hang detector (ADR-0072 decision 6): on SIGALRM the
    handler asks pacing how much wall time was governed sleep and re-arms
    the timer for the remainder instead of killing the run.
    """
    if (
        seconds is None
        or not math.isfinite(seconds)
        or seconds <= 0
        or threading.current_thread() is not threading.main_thread()
    ):
        yield
        return

    from tgcli.governor import pacing

    base_slept = pacing.total_governed_sleep()
    started = time.monotonic()

    def fire(signum, frame):
        elapsed = time.monotonic() - started
        governed = pacing.total_governed_sleep() - base_slept
        remaining = seconds - (elapsed - governed)
        if remaining > 0:
            signal.setitimer(signal.ITIMER_REAL, remaining + DEADLINE_GRACE)
            return
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
    """Run one coroutine under `--timeout` as the documented TIMEOUT error.

    Governed sleep is discounted (ADR-0072 decision 6): the loop re-reads
    pacing's slept total on every wake and grants it back, so a run pacing
    itself out of a flood is not killed for doing the right thing.
    """
    from tgcli.governor import pacing

    async def run():
        if timeout is None:
            return await coro
        task = asyncio.create_task(coro)
        started = time.monotonic()
        base_slept = pacing.total_governed_sleep()
        while True:
            governed = pacing.total_governed_sleep() - base_slept
            remaining = timeout - (time.monotonic() - started - governed)
            if remaining <= 0:
                # The command's own deadline (transcribe's wait_for) may still
                # win inside the grace window and raise its detailed
                # CommandTimeoutError; fall back to the generic TIMEOUT only
                # once that window is over. The SIGALRM backstop is disarmed
                # first so its `seconds + grace` firing cannot race this wait.
                signal.setitimer(signal.ITIMER_REAL, 0)
                done, _ = await asyncio.wait({task}, timeout=DEADLINE_GRACE)
                if done:
                    try:
                        return task.result()
                    except asyncio.CancelledError:
                        raise TimeoutError from None
                task.cancel()
                raise TimeoutError
            done, _ = await asyncio.wait({task}, timeout=remaining)
            if done:
                return task.result()

    try:
        return asyncio.run(run())
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
    if args.command == "transcribe":
        safety.append_audit(
            args.command,
            account.alias,
            {"chat": args.chat, "message_id": args.message_id},
        )
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
        args.command == "clone"
        and args.clone_command in ("init", "refresh")
        and getattr(args, "commit", None)
    ):
        # Only a finished commit spends the preview; a flood partway through
        # leaves it .pending so the same commit can be retried (#170).
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


def _execute(args) -> tuple[dict, list[tuple]]:
    """Run one prepared invocation, opening only the resources it needs."""
    if args.command == "accounts" and args.subcommand == "import":
        data = accounts_cmd.import_accounts(
            args.aliases or None, args.source_root.expanduser(), force=args.force
        )
        return data, accounts_cmd.import_rows(data)
    if args.command == "clone" and args.clone_command == "status":
        data = clone_cmd.list_clones(args.source, include_all=args.all_slots)
        if data["pending_import"] and not args.all_slots:
            output.note(
                f"{data['pending_import']} state slot(s) cannot be imported and "
                "are not listed; see them with: tg clone status --all"
            )
        for entry in data["clones"]:
            if clone_cmd.half_initialized(entry):
                output.note(
                    f"clone {entry['source']['id']} has comments enabled but no "
                    "linked discussion group; finish it with: tg clone init "
                    f"{entry['source']['id']}"
                )
        return data, clone_cmd.status_rows(data)
    if args.command == "clone" and args.clone_command == "export-state":
        data = clone_cmd.export_state(args.source)
        return data, []
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
    if args.command == "jobs":
        if args.jobs_command == "run" and getattr(args, "rearm", None) is not None:
            alias = jobs_cmd.resolve_alias(args.account, config)
            current = jobs_cmd.show(alias, args.rearm)["job"]
            jobs_preflight.prepare_resolved_run(args, current["lane"])
            jobs_cmd.rearm(alias, args.rearm, expected_lane=current["lane"])
        offline = jobs_cmd.execute_offline(args, config)
        if offline is not None:
            return offline
    if args.command == "archive" and args.archive_command in (
        "list",
        "status",
        "search",
        "read",
        "history",
        "transcribe",
    ):
        alias = archive_cmd.resolve_alias(args.account, config)
        if args.archive_command == "list":
            data = archive_cmd.list_scope(alias, config)
            return data, archive_cmd.list_rows(data)
        if args.archive_command == "status":
            data = archive_cmd.status(alias, config)
            return data, archive_cmd.status_rows(data)
        if args.archive_command == "transcribe":
            data = archive_cmd.transcribe(
                alias,
                limit=getattr(args, "limit", None),
                max_attempts=getattr(args, "max_attempts", None),
                config=config,
            )
            return data, archive_cmd.transcribe_rows(data)
        if args.archive_command == "read":
            data = archive_cmd.read(
                alias,
                args.chat,
                around_id=getattr(args, "around_id", None),
                around_date=getattr(args, "around_date", None),
                since=getattr(args, "since", None),
                until=getattr(args, "until", None),
                limit=getattr(args, "limit", None),
                config=config,
            )
            return data, archive_cmd.read_rows(data)
        if args.archive_command == "history":
            data = archive_cmd.history(alias, args.chat, args.message_id, config=config)
            return data, archive_cmd.history_rows(data)
        data = archive_cmd.search(
            alias,
            args.query,
            chat=getattr(args, "chat", None),
            from_user=getattr(args, "from_user", None),
            since=getattr(args, "since", None),
            until=getattr(args, "until", None),
            kind=getattr(args, "kind", None),
            transcripts_only=bool(getattr(args, "transcripts_only", False)),
            sort=getattr(args, "sort", "relevance"),
            limit=getattr(args, "limit", None),
            page=getattr(args, "page", None),
            config=config,
        )
        return data, archive_cmd.search_rows(data)
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
                    phone=args.phone,
                    api_id=getattr(args, "api_id", None),
                    api_hash=getattr(args, "api_hash", None),
                    force=bool(getattr(args, "force", False)),
                    role=getattr(args, "login_role", None),
                )
            )
        return data, login_cmd.login_rows(data)
    if args.command == "accounts":
        data = accounts_cmd.list_accounts(config)
        return data, accounts_cmd.to_rows(data)
    if args.command == "doctor":
        connect = bool(getattr(args, "connect", False))
        coro = doctor_cmd.run(
            config, args.account, connect=connect, readonly=args.readonly
        )
        if connect:
            data = _run_with_deadline(coro, args.timeout)
        else:
            data = asyncio.run(coro)
        return data, doctor_cmd.to_rows(data)

    account = resolve_account(config, args.account)
    args.account = account.alias
    if args.verbose:
        LOGGER.debug("resolved account=%s command=%s", account.alias, args.command)
    # The audit role must cover every append_audit call this invocation makes,
    # not just the network coroutine's client window: _audit_before/_audit_after
    # below run outside dispatch.run_network, so setting it only there (as
    # dispatch used to) left the cli-level mutation audit rows without "role"
    # (CONTRACT.md §9, ADR-0062).
    token = safety.set_audit_role(getattr(args, "session_role", None))
    try:
        _audit_before(args, account)
        network = _run_network(args, account)
        data, rows = _run_with_deadline(network, args.timeout)
        _audit_after(args, account, data)
        return data, rows
    finally:
        safety.reset_audit_role(token)


def _emit(args, data, rows) -> None:
    if args.command == "clone" and args.clone_command == "export-state":
        # CONTRACT: export-state's stdout IS the v2 JSON document (ADR-0060).
        output.emit_json(data)
        return
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
    _apply_global_defaults(args)
    from tgcli.governor import pacing

    pacing.reset_runtime(cap=getattr(args, "max_runtime", None))
    verbose_diagnostics = _enable_verbose_diagnostics() if args.verbose else []
    started = time.monotonic()
    exit_code = 1
    error_code = None
    result_data: dict | None = None
    try:
        with _honest_termination():
            with _armed(args.timeout):
                preflight.prepare(parser, args)
                data, rows = _execute(args)
            result_data = data
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
        # The whole-body alarm fired; the asyncio deadline is already
        # translated by _run_with_deadline (with its grace window).
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
        stop_reason = None
        if isinstance(result_data, dict):
            stop_reason = result_data.get("stop_reason")
        if stop_reason is not None:
            stop_fields = {"stop_reason": stop_reason}
        elif exit_code != 0:
            # A flood-family stop (refusal or unhandled flood) only counts
            # when the run actually ended on it; a flood that was slept out
            # and survived is not a flood-related exit (plan phase 6).
            stop_fields = pacing.last_stop() or {}
        else:
            stop_fields = {}
        # Governor accounting only belongs on runs that actually governed
        # requests (review fix m1): offline commands carry neither field.
        slept_ms = int(pacing.total_governed_sleep() * 1000)
        requests = pacing.request_count()
        jobs_fields = {}
        if (
            args.command == "jobs"
            and args.jobs_command == "run"
            and isinstance(result_data, dict)
        ):
            jobs_fields = {
                "lane": result_data["lane"],
                "selected": result_data["selected"],
                "completed": result_data["completed"],
                "deferred": result_data["queued"],
                "failed": result_data["failed"],
                "cancelled": result_data["cancelled"],
            }
        invocations.log_invocation(
            command=args.command,
            account=args.account,
            role=getattr(args, "session_role", None),
            exit_code=exit_code,
            error=error_code,
            duration_ms=duration_ms,
            governed_sleep_ms=slept_ms if requests else None,
            request_count=requests or None,
            **stop_fields,
            **jobs_fields,
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
