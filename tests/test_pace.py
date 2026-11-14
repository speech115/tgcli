"""`tgcli.pace`: spacing for one loop's requests, and the `--max-runtime` cap."""

from __future__ import annotations

import asyncio

import pytest
from hypothesis import given, settings, strategies as st
from telethon import errors as telethon_errors

from tgcli import pace
from tgcli.pace import Pace

DETERMINISTIC = settings(max_examples=200, derandomize=True)


class Clock:
    """Fake time: a sleep advances it, and so does the caller between calls."""

    def __init__(self):
        self.now = 0.0
        self.sleeps = []

    def clock(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


async def _let_through(interval, free, gaps) -> tuple[list[float], list[float], Clock]:
    """Call `wait()` once per gap; return when each call arrived and left."""
    time = Clock()
    spacing = Pace(interval, free=free, sleep=time.sleep, clock=time.clock)
    arrived, left = [], []
    for gap in gaps:
        time.now += gap
        arrived.append(time.now)
        await spacing.wait()
        left.append(time.now)
    return arrived, left, time


@DETERMINISTIC
@given(
    interval=st.floats(min_value=0.1, max_value=30),
    free=st.integers(min_value=0, max_value=8),
    gaps=st.lists(st.floats(min_value=0, max_value=60), min_size=1, max_size=40),
)
def test_calls_past_the_free_ones_start_at_least_an_interval_apart(
    interval, free, gaps
):
    arrived, left, time = asyncio.run(_let_through(interval, free, gaps))

    assert left[: max(free, 1)] == arrived[: max(free, 1)], "a free call waited"
    for index in range(max(free, 1), len(left)):
        assert left[index] - left[index - 1] >= interval - 1e-9
    assert all(wait <= interval + 1e-9 for wait in time.sleeps)


async def test_a_loop_slower_than_the_interval_never_waits():
    _, _, time = await _let_through(3.0, 0, [0, 3, 5, 3.5])

    assert time.sleeps == []


async def test_a_tight_loop_waits_the_interval_after_its_free_calls():
    _, left, time = await _let_through(3.0, 2, [0, 0, 0, 0])

    assert left == [0, 0, 3.0, 6.0]
    assert time.sleeps == [3.0, 3.0]


async def test_concurrent_calls_queue_instead_of_sharing_one_slot():
    sleeps = []

    async def sleep(seconds):
        sleeps.append(seconds)
        await asyncio.sleep(0)

    spacing = Pace(3.0, free=0, sleep=sleep, clock=lambda: 0.0)

    await asyncio.gather(spacing.wait(), spacing.wait(), spacing.wait())

    assert sleeps == [3.0, 6.0]


async def _rows(count):
    for index in range(count):
        yield index


async def test_paged_yields_every_row_and_waits_once_per_further_page(pace_clock):
    rows = [row async for row in pace.paged(_rows(25), 10, pace.Pace(3.0, free=0))]

    assert rows == list(range(25))
    # The first page boundary opens the spacing; the second one waits.
    assert pace_clock.sleeps == [3.0]


async def test_paged_by_default_lets_the_first_pages_through(pace_clock):
    rows = [row async for row in pace.paged(_rows(700), 100)]

    assert len(rows) == 700
    assert pace_clock.sleeps == [3.0, 3.0]


def test_no_max_runtime_is_never_exhausted():
    pace.reset()

    assert pace.remaining() is None
    assert pace.exhausted() is False


def test_max_runtime_counts_down_from_reset():
    pace.reset(60)
    left = pace.remaining()

    assert left is not None and 59 < left <= 60
    assert pace.exhausted() is False


def test_a_spent_max_runtime_is_exhausted():
    pace.reset(0)

    assert pace.exhausted() is True


class Sender:
    """A ``send`` that raises one flood per entry of ``floods``, then answers."""

    def __init__(self, *floods):
        self.floods = list(floods)
        self.calls = 0

    async def __call__(self):
        self.calls += 1
        if self.floods:
            raise self.floods.pop(0)
        return "sent"


def _flood(seconds, premium=False):
    kind = (
        telethon_errors.FloodPremiumWaitError
        if premium
        else telethon_errors.FloodWaitError
    )
    return kind(request=None, capture=seconds)


@pytest.mark.parametrize("premium", [False, True], ids=["flood", "premium-flood"])
async def test_call_resends_once_after_a_flood_it_can_sleep(premium):
    time = Clock()
    spacing = Pace(3.0, sleep=time.sleep, clock=time.clock)
    send = Sender(_flood(pace.FLOOD_SLEEP_THRESHOLD, premium))

    assert await spacing.call(send) == "sent"

    assert send.calls == 2
    assert time.sleeps == [pace.FLOOD_SLEEP_THRESHOLD]


async def test_call_without_a_flood_sends_once_and_sleeps_nothing():
    time = Clock()
    send = Sender()

    assert await Pace(3.0, sleep=time.sleep, clock=time.clock).call(send) == "sent"

    assert send.calls == 1
    assert time.sleeps == []


async def test_call_raises_a_flood_past_the_threshold_unchanged():
    time = Clock()
    flood = _flood(pace.FLOOD_SLEEP_THRESHOLD + 1)
    send = Sender(flood)

    with pytest.raises(telethon_errors.FloodWaitError) as raised:
        await Pace(3.0, sleep=time.sleep, clock=time.clock).call(send)

    assert raised.value is flood
    assert send.calls == 1
    assert time.sleeps == []


async def test_call_raises_a_second_flood():
    time = Clock()
    second = _flood(5)
    send = Sender(_flood(5), second)

    with pytest.raises(telethon_errors.FloodWaitError) as raised:
        await Pace(3.0, sleep=time.sleep, clock=time.clock).call(send)

    assert raised.value is second
    assert send.calls == 2


async def test_call_does_not_sleep_past_the_max_runtime_cap():
    time = Clock()
    pace.reset(4)
    send = Sender(_flood(5))

    with pytest.raises(telethon_errors.FloodWaitError):
        await Pace(3.0, sleep=time.sleep, clock=time.clock).call(send)

    assert send.calls == 1
    assert time.sleeps == []


async def test_call_sleeps_a_flood_that_fits_the_max_runtime_cap():
    time = Clock()
    pace.reset(60)
    send = Sender(_flood(5))

    assert await Pace(3.0, sleep=time.sleep, clock=time.clock).call(send) == "sent"

    assert time.sleeps == [5]
