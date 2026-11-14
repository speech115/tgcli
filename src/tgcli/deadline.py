"""The `--timeout` invocation deadline (CONTRACT §1).

Two halves: `armed` holds a SIGALRM backstop over the whole invocation, and
`run` bounds one network coroutine. Both count plain wall time.
"""

import asyncio
import contextlib
import math
import signal
import threading

from tgcli import loop
from tgcli.errors import CommandTimeoutError

# The deadline is armed before preflight, so it must not preempt a command that
# owns a graceful asyncio deadline around the network. Those arm later; this
# margin keeps them first.
GRACE = 1.0


class AlarmFired(BaseException):
    """SIGALRM escape: a BaseException so no `except Exception` swallows it."""


@contextlib.contextmanager
def armed(seconds: float | None):
    """Hold the invocation deadline over the whole body, preflight included.

    asyncio.wait_for covers only the network coroutine; stdin reads etc.
    would otherwise run with no deadline at all.
    """
    if (
        seconds is None
        or not math.isfinite(seconds)
        or seconds <= 0
        or threading.current_thread() is not threading.main_thread()
    ):
        yield
        return

    def fire(signum, frame):
        raise AlarmFired

    previous = signal.signal(signal.SIGALRM, fire)
    signal.setitimer(signal.ITIMER_REAL, seconds + GRACE)
    try:
        yield
    finally:
        signal.setitimer(signal.ITIMER_REAL, 0)
        signal.signal(signal.SIGALRM, previous)


class _Expired(Exception):
    """The `--timeout` deadline ran out inside `run`.

    Private so that a command's own `TimeoutError` (a script's `wait_for`, a
    socket timeout) is not mistaken for the invocation deadline.
    """


def run(coro, timeout):
    """Run one coroutine under `--timeout` as the documented TIMEOUT error."""

    async def bounded():
        if timeout is None:
            return await coro
        task = asyncio.create_task(coro)
        done, _ = await asyncio.wait({task}, timeout=timeout)
        if done:
            return task.result()
        # The command's own deadline (transcribe's wait_for) may still win
        # inside the grace window and raise its detailed CommandTimeoutError;
        # fall back to the generic TIMEOUT only once that window is over. The
        # SIGALRM backstop is disarmed first so its `seconds + grace` firing
        # cannot race this wait.
        signal.setitimer(signal.ITIMER_REAL, 0)
        done, _ = await asyncio.wait({task}, timeout=GRACE)
        if done:
            try:
                return task.result()
            except (asyncio.CancelledError, TimeoutError):
                # Past the deadline, a builtin TimeoutError (a socket timing
                # out in the grace window) is the deadline too.
                raise _Expired from None
        task.cancel()
        # Let its `finally` blocks run before the next step does: asyncio.run
        # waited for them, a held loop (tg-serve) would not.
        await asyncio.wait({task})
        raise _Expired

    try:
        return loop.run(bounded())
    except _Expired:
        raise CommandTimeoutError(
            f"invocation exceeded the --timeout deadline of {timeout}s"
        ) from None
