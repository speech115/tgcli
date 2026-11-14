"""Request spacing for bulk loops, and the `--max-runtime` cap (ADR-0125).

Telethon sleeps a FloodWait of up to `FLOOD_SLEEP_THRESHOLD` seconds and
raises a longer one, which exits 5 with `retry_after`. Neither keeps a loop
from drawing the flood, so every loop that sends many requests spaces them
here. A command that sends a handful never waits.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import AsyncIterable, AsyncIterator, Awaitable, Callable

from tgcli.tg_errors import FLOOD_ERRORS

# Telethon sleeps a FloodWait up to this long and sends the request again; a
# longer one raises, and the command exits 5 with `retry_after` (ADR-0125).
# It also remembers the wait per request type for the rest of the process: a
# further request of that type sleeps out what is left, or raises unsent.
# `Pace.call` resends a write after a flood of up to this long as well.
FLOOD_SLEEP_THRESHOLD = 30

# Between the pages of one history or enumeration walk, and between the peers
# of a loop that walks many. The 2026-07-31 flood was 791 dialogs read with
# no gap at all.
PAGE_WAIT_S = 3.0
# A command that reads one chat at the owner's request. `None` is Telethon's
# own default: no wait up to 3000 messages, 1 s a page beyond.
INTERACTIVE_PAGE_WAIT = None
# 20 sends a minute, the pace a clone held without a flood (ADR-0122).
SEND_INTERVAL_S = 3.0
MEDIA_INTERVAL_S = 3.0

# Rows one request returns from `iter_dialogs` and `iter_participants`.
DIALOGS_PAGE = 100
PARTICIPANTS_PAGE = 200


class Pace:
    """Start-to-start spacing for the requests of one loop.

    The first ``free`` calls go at once; each later one waits until
    ``interval`` seconds after the previous call was let through.
    """

    def __init__(
        self,
        interval: float,
        *,
        free: int = 5,
        sleep=asyncio.sleep,
        clock=time.monotonic,
    ) -> None:
        self._interval = interval
        self._free = free
        self._sleep = sleep
        self._clock = clock
        self._calls = 0
        self._last: float | None = None

    async def wait(self) -> None:
        self._calls += 1
        now = self._clock()
        start = now
        if self._calls > self._free and self._last is not None:
            start = max(now, self._last + self._interval)
        # Reserved before the sleep, so concurrent tasks queue behind this
        # call instead of computing the same wait.
        self._last = start
        if start > now:
            await self._sleep(start - now)

    async def call[T](self, send: Callable[[], Awaitable[T]]) -> T:
        """Let ``send()`` through, and resend it once after a short flood.

        A FloodWait means Telegram did not run the request, so sending it
        again cannot duplicate it; a timeout or a 500 could, and propagates.
        A second flood, or one past the threshold or the `--max-runtime`
        cap, propagates unchanged.
        """
        await self.wait()
        try:
            return await send()
        except FLOOD_ERRORS as flood:
            left = remaining()
            if flood.seconds > FLOOD_SLEEP_THRESHOLD or (
                left is not None and flood.seconds > left
            ):
                raise
            await self._sleep(flood.seconds)
        return await send()


async def paged[T](
    rows: AsyncIterable[T], page_size: int, pace: Pace | None = None
) -> AsyncIterator[T]:
    """Yield ``rows``, waiting before each further page.

    For the Telethon iterators that take no ``wait_time``: dialogs and
    participants. ``iter_messages`` takes ``wait_time`` instead.
    """
    pace = pace or Pace(PAGE_WAIT_S)
    seen = 0
    async for row in rows:
        yield row
        seen += 1
        if seen % page_size == 0:
            await pace.wait()


_cap: float | None = None
_started = 0.0


def reset(cap: float | None = None) -> None:
    """Start this invocation's `--max-runtime` clock; ``None`` is no cap."""
    global _cap, _started
    _cap = cap
    _started = time.monotonic()


def remaining() -> float | None:
    """Seconds left under `--max-runtime`, or ``None`` when no cap is set."""
    if _cap is None:
        return None
    return _cap - (time.monotonic() - _started)


def exhausted() -> bool:
    """Whether `--max-runtime` was set and has run out."""
    left = remaining()
    return left is not None and left <= 0
