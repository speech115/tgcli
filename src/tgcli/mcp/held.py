"""Run `tg-mcp` tool calls in-process over one held, idle-released session.

A `tg` process per tool call pays the interpreter start, the session lock, the
connect and Telethon's startup requests every time. This runner keeps one
client connected across a burst of calls instead (the `tg-serve` mechanism,
ADR-0124) and lets it go after `idle` seconds without a call, so a `tg` command
or a launchd job on the same session file waits at most that long.

Every call is still a full `tg --json --readonly` invocation through
`cli.main`: parsing, preflight, the readonly gates, the invocation journal.
"""

from __future__ import annotations

import asyncio
import contextlib
import io
import os
import sys
import threading
from typing import Any

from tgcli import loop, output, serve, session
from tgcli.config import load_config, resolve_account
from tgcli.errors import TgcliError
from tgcli.parser import build_parser

DEFAULT_IDLE = 60.0
# The session role tg-mcp holds unless told otherwise, so `tg` and launchd jobs
# on the account's main session never wait for an agent.
DEFAULT_ROLE = "mcp"


class HeldRunner:
    """A `Runner` (argv -> exit code, stdout) that reuses one Telegram session."""

    def __init__(
        self,
        account: str | None = None,
        *,
        role: str | None = None,
        timeout: float | None = None,
        idle: float = DEFAULT_IDLE,
        fallback_to_main: bool = False,
    ) -> None:
        # The process exists to serve read-only tools: no call may write.
        os.environ["TGCLI_READONLY"] = "1"
        os.environ["TGCLI_NO_SEND"] = "1"
        self._alias = account
        self._role = role
        self._fallback_to_main = fallback_to_main
        self._timeout = timeout
        self._idle = idle
        self._parser = build_parser()
        self._lock = threading.Lock()
        self._held: contextlib.AbstractAsyncContextManager[Any] | None = None
        self._prefix: list[str] = []
        self._timer: threading.Timer | None = None
        self._generation = 0
        self._event_loop = asyncio.new_event_loop()
        self._thread = threading.Thread(
            target=self._event_loop.run_forever, name="tg-mcp", daemon=True
        )
        self._thread.start()
        self._routing = loop.running_on(self._event_loop)
        self._routing.__enter__()

    def __call__(self, argv: list[str]) -> tuple[int, str]:
        with self._lock:
            self._cancel_timer()
            self._generation += 1
            try:
                self._open()
            except TgcliError as err:
                return err.exit_code, _envelope(err)
            flags = ["--json", "--readonly", *self._timeout_flag()]
            reply = serve.invoke([*self._prefix, *flags, *argv], self._parser)
            self._arm_timer()
        if reply["stderr"]:
            sys.stderr.write(reply["stderr"])
        return reply["exit_code"], reply["stdout"]

    @property
    def holding(self) -> bool:
        return self._held is not None

    def close(self) -> None:
        """Release the session and stop the loop; the runner is done."""
        with self._lock:
            self._cancel_timer()
            self._release()
        self._routing.__exit__(None, None, None)
        self._event_loop.call_soon_threadsafe(self._event_loop.stop)
        self._thread.join(timeout=5)

    def _open(self) -> None:
        if self._held is not None:
            return
        account = resolve_account(load_config(), self._alias)
        role = self._role
        if (
            role is not None
            and self._fallback_to_main
            and not session.session_path(account, role).is_file()
        ):
            output.note(
                f"tg-mcp: session role {role!r} is not authorized, so the main "
                f"session is held and tg waits while it is; run: tg accounts "
                f"login {account.alias} --role {role} --phone PHONE"
            )
            role = None
        held = session.hold(account, role=role)
        loop.run(held.__aenter__())
        self._held = held
        role_flag = ["--session-role", role] if role else []
        self._prefix = ["--account", account.alias, *role_flag]

    def _release(self) -> None:
        # `holding` turns false only once the session lock is released.
        held = self._held
        if held is None:
            return
        try:
            loop.run(held.__aexit__(None, None, None))
        finally:
            self._held = None

    def _timeout_flag(self) -> list[str]:
        return ["--timeout", str(self._timeout)] if self._timeout else []

    def _arm_timer(self) -> None:
        self._generation += 1
        self._timer = threading.Timer(self._idle, self._on_idle, [self._generation])
        self._timer.daemon = True
        self._timer.start()

    def _cancel_timer(self) -> None:
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None

    def _on_idle(self, generation: int) -> None:
        with self._lock:
            # A call that started after this timer fired armed a newer one.
            if generation != self._generation:
                return
            self._timer = None
            self._release()


def _envelope(err: TgcliError) -> str:
    """The error document `tg --json` prints for `err`."""
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(io.StringIO()):
        output.emit_error(err, as_json=True)
    return out.getvalue()
