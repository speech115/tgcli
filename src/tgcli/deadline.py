"""The `--timeout` invocation deadline (CONTRACT §1).

Two halves: `armed` holds a SIGALRM backstop over the whole invocation, and
`run` bounds one network coroutine. Both are hang detectors that grant back
governed sleep (ADR-0072 decision 6), so a run pacing itself out of a flood is
not killed for doing the right thing.
"""

import asyncio
import contextlib
import math
import signal
import threading
import time

from tgcli import loop
from tgcli.errors import CommandTimeoutError
from tgcli.governor import pacing

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

    base_slept = pacing.total_governed_sleep()
    started = time.monotonic()

    def fire(signum, frame):
        elapsed = time.monotonic() - started
        governed = pacing.total_governed_sleep() - base_slept
        remaining = seconds - (elapsed - governed)
        if remaining > 0:
            signal.setitimer(signal.ITIMER_REAL, remaining + GRACE)
            return
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
    """Run one coroutine under `--timeout` as the documented TIMEOUT error.

    The loop re-reads pacing's slept total on every wake and grants it back.
    """

    async def bounded():
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
                done, _ = await asyncio.wait({task}, timeout=GRACE)
                if done:
                    try:
                        return task.result()
                    except (asyncio.CancelledError, TimeoutError):
                        # Past the deadline, a builtin TimeoutError (a socket
                        # timing out in the grace window) is the deadline too.
                        raise _Expired from None
                task.cancel()
                # Let its `finally` blocks run before the next step does:
                # asyncio.run waited for them, a held loop (tg-serve) would not.
                await asyncio.wait({task})
                raise _Expired
            done, _ = await asyncio.wait({task}, timeout=remaining)
            if done:
                return task.result()

    try:
        return loop.run(bounded())
    except _Expired:
        raise CommandTimeoutError(
            f"invocation exceeded the --timeout deadline of {timeout}s"
        ) from None
