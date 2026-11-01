"""Slow start: a request type that drew a flood recently is paced from its
first request in the next process, at a longer interval.

Most floods in the journal repeated a flood of the same type within the
hour: the run exited 5, the next run started with fresh free requests and
burst into the same limit. A fake server with a per-type sliding-window
limit stands in for Telegram; no test sleeps in real time.
"""

from datetime import UTC, datetime, timedelta

import pytest
from telethon import errors as telethon_errors
from telethon.tl.functions import messages, users

from tgcli.governor import gate, pacing
from tgcli.governor.ledger import Ledger

ACCOUNT = 7091037467
USERS_KEY = "users.GetUsersRequest"
HISTORY_KEY = "messages.GetHistoryRequest"


def get_users():
    return users.GetUsersRequest(id=[])


def history():
    return messages.GetHistoryRequest(
        peer="p",
        offset_id=0,
        offset_date=None,
        add_offset=0,
        limit=100,
        max_id=0,
        min_id=0,
        hash=0,
    )


class LimitedServer:
    """Floods a request type past `limit` requests within `window` seconds."""

    def __init__(self, clock, *, limit=8, window=10.0, wait=5):
        self._self_id = ACCOUNT
        self.clock = clock
        self.limit = limit
        self.window = window
        self.wait = wait
        self.sent: dict[str, list[float]] = {}
        self.floods = 0

    async def _call(self, sender, request, *args, **kwargs):
        now = self.clock["t"]
        key = type(request).__name__
        recent = [t for t in self.sent.get(key, []) if t > now - self.window]
        if len(recent) >= self.limit:
            self.floods += 1
            error = telethon_errors.FloodWaitError(request=None)
            error.seconds = self.wait
            raise error
        self.sent[key] = [*recent, now]
        return "result"


@pytest.fixture
def clock():
    return {"t": 0.0}


@pytest.fixture
def sleeper(clock):
    calls = []

    async def fake_sleep(seconds):
        calls.append(seconds)
        clock["t"] += seconds

    fake_sleep.calls = calls
    return fake_sleep


@pytest.fixture(autouse=True)
def clean_pacing_state():
    pacing.reset_runtime()
    yield
    pacing.reset_runtime()


@pytest.fixture
def ledger(tmp_path):
    with Ledger.open(tmp_path / "governor.db") as store:
        yield store


def flooded_ago(ledger, key, minutes):
    """Record a flood on `key` armed `minutes` ago whose cooldown is over."""
    now = datetime.now(UTC)
    ledger.arm_cooldown(
        ACCOUNT, key, now - timedelta(seconds=1), now=now - timedelta(minutes=minutes)
    )


def server(ledger, clock, sleeper, **limits):
    fake = LimitedServer(clock, **limits)
    gate.install(fake, ledger, sleep=sleeper, clock=lambda: clock["t"])
    return fake


async def test_the_run_after_a_flood_does_not_burst_into_it_again(
    ledger, clock, sleeper
):
    """The restart loop: run one bursts an unpaced type into the limit and
    exits 5; run two, after the cooldown, must not repeat the burst."""
    first = server(ledger, clock, sleeper)
    with pytest.raises(telethon_errors.FloodWaitError):
        for _ in range(12):
            await first._call(None, get_users())
    assert first.floods == 1

    flooded_ago(ledger, USERS_KEY, minutes=1)
    clock["t"] += 60
    pacing.reset_runtime()
    second = server(ledger, clock, sleeper)
    for _ in range(12):
        await second._call(None, get_users())

    assert second.floods == 0


async def test_a_recently_flooded_type_waits_from_its_first_request(
    ledger, clock, sleeper
):
    flooded_ago(ledger, USERS_KEY, minutes=10)
    fake = server(ledger, clock, sleeper, limit=1000)

    for _ in range(3):
        await fake._call(None, get_users())

    assert sleeper.calls == [pacing.SLOW_START_INTERVAL] * 2


async def test_a_recently_flooded_paced_type_waits_twice_its_interval(
    ledger, clock, sleeper
):
    flooded_ago(ledger, HISTORY_KEY, minutes=10)
    fake = server(ledger, clock, sleeper, limit=1000)

    for _ in range(3):
        await fake._call(None, history())

    assert sleeper.calls == [6.0, 6.0]


async def test_an_old_flood_leaves_the_free_requests_alone(ledger, clock, sleeper):
    flooded_ago(ledger, USERS_KEY, minutes=61)
    fake = server(ledger, clock, sleeper, limit=1000)

    for _ in range(pacing.FREE_REQUESTS + 3):
        await fake._call(None, get_users())

    assert sleeper.calls == []


async def test_a_flood_on_one_type_does_not_slow_another(ledger, clock, sleeper):
    flooded_ago(ledger, HISTORY_KEY, minutes=10)
    fake = server(ledger, clock, sleeper, limit=1000)

    for _ in range(pacing.FREE_REQUESTS):
        await fake._call(None, get_users())

    assert sleeper.calls == []
