"""Clone cooldown: short FloodWait retry and per-run wait budget (ADR-0052)."""

import asyncio
from datetime import UTC, datetime

import pytest
from telethon import errors as telethon_errors

from tgcli.clone import flood, state
from tgcli.commands import clone as clone_cmd


@pytest.fixture
def clone_state(state_dir_env):
    saved = state.CloneState.new(
        account_user_id=42,
        source_peer_id=100,
        source_title="Source",
    )
    saved.destination_peer_id = 200
    state.save(saved)
    return saved


@pytest.fixture
def no_sleep(monkeypatch):
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)

    monkeypatch.setattr("tgcli.clone.cooldown.asyncio.sleep", fake_sleep)
    return sleeps


@pytest.fixture
def budget():
    return flood.WaitBudget()


@pytest.mark.asyncio
async def test_with_cooldown_long_flood_raises_without_sleep(
    clone_state, no_sleep, budget
):
    attempts = 0

    async def boom():
        nonlocal attempts
        attempts += 1
        raise telethon_errors.FloodWaitError(request=None, capture=61)

    with pytest.raises(telethon_errors.FloodWaitError) as raised:
        await clone_cmd._with_cooldown(boom, clone_state, budget)

    assert raised.value.seconds == 61
    assert attempts == 1
    assert no_sleep == []
    assert clone_state.cooldown_deadline() is not None


@pytest.mark.asyncio
async def test_with_cooldown_awaits_fresh_thunk_result(clone_state, budget):
    calls = 0

    async def ok():
        nonlocal calls
        calls += 1
        return "done"

    assert await clone_cmd._with_cooldown(ok, clone_state, budget) == "done"
    assert calls == 1


@pytest.mark.asyncio
async def test_short_flood_sleeps_once_then_retries(clone_state, no_sleep, budget):
    attempts = 0

    async def once_then_ok():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise telethon_errors.FloodWaitError(request=None, capture=3)
        return "ok"

    assert await clone_cmd._with_cooldown(once_then_ok, clone_state, budget) == "ok"
    assert attempts == 2
    assert no_sleep == [4]
    assert budget.spent == 4


@pytest.mark.asyncio
async def test_second_flood_propagates_without_another_sleep(
    clone_state, no_sleep, budget
):
    attempts = 0

    async def always_flood():
        nonlocal attempts
        attempts += 1
        raise telethon_errors.FloodWaitError(
            request=None, capture=5 if attempts == 1 else 9
        )

    with pytest.raises(telethon_errors.FloodWaitError) as raised:
        await clone_cmd._with_cooldown(always_flood, clone_state, budget)

    assert raised.value.seconds == 9
    assert attempts == 2
    assert no_sleep == [6]
    deadline = clone_state.cooldown_deadline()
    assert deadline is not None
    account_deadline = flood.cooldown_deadline(clone_state.account_user_id)
    assert account_deadline is not None
    remaining = (deadline - datetime.now(UTC)).total_seconds()
    assert remaining > 8


@pytest.mark.asyncio
async def test_cooldown_is_armed_before_sleep(clone_state, monkeypatch, budget):
    armed_at_sleep: list[datetime | None] = []

    async def fake_sleep(seconds):
        armed_at_sleep.append(flood.cooldown_deadline(clone_state.account_user_id))

    monkeypatch.setattr("tgcli.clone.cooldown.asyncio.sleep", fake_sleep)

    async def once_then_ok():
        if not hasattr(once_then_ok, "n"):
            once_then_ok.n = 0
        once_then_ok.n += 1
        if once_then_ok.n == 1:
            raise telethon_errors.FloodWaitError(request=None, capture=2)
        return "ok"

    assert await clone_cmd._with_cooldown(once_then_ok, clone_state, budget) == "ok"
    assert len(armed_at_sleep) == 1
    assert armed_at_sleep[0] is not None
    assert armed_at_sleep[0] > datetime.now(UTC)


@pytest.mark.asyncio
async def test_short_flood_wait_prints_stderr_seconds(
    clone_state, no_sleep, capsys, budget
):
    async def once_then_ok():
        if not hasattr(once_then_ok, "n"):
            once_then_ok.n = 0
        once_then_ok.n += 1
        if once_then_ok.n == 1:
            raise telethon_errors.FloodWaitError(request=None, capture=7)
        return "ok"

    assert await clone_cmd._with_cooldown(once_then_ok, clone_state, budget) == "ok"
    err = capsys.readouterr().err
    assert "7" in err
    assert "flood" in err.lower() or "wait" in err.lower()


def test_wait_budget_allows_spending_until_limit():
    budget = flood.WaitBudget()
    assert budget.try_spend(180) is True
    assert budget.spent == 180
    assert budget.try_spend(1) is False
    assert budget.spent == 180


def test_wait_budget_refuses_when_already_at_limit():
    budget = flood.WaitBudget()
    assert budget.try_spend(100) is True
    assert budget.try_spend(80) is True
    assert budget.spent == 180
    assert budget.try_spend(1) is False


def test_wait_budget_refuses_spend_that_would_exceed_limit():
    """CONTRACT §11 / ADR-0052: at most 180s of pausing — not spent+next > 180."""
    budget = flood.WaitBudget()
    assert budget.try_spend(179) is True
    assert budget.try_spend(2) is False
    assert budget.spent == 179


@pytest.mark.asyncio
async def test_short_floods_retry_while_under_wait_budget(clone_state, no_sleep):
    attempts = 0

    async def flood_then_ok():
        nonlocal attempts
        attempts += 1
        if attempts % 2 == 1:
            raise telethon_errors.FloodWaitError(request=None, capture=3)
        return "ok"

    budget = flood.WaitBudget()
    for _ in range(3):
        assert (
            await clone_cmd._with_cooldown(flood_then_ok, clone_state, budget) == "ok"
        )
    assert no_sleep == [4, 4, 4]
    assert budget.spent == 12


@pytest.mark.asyncio
async def test_spent_wait_budget_refuses_even_a_one_second_flood(clone_state, no_sleep):
    budget = flood.WaitBudget()
    assert budget.try_spend(180) is True

    async def short_flood():
        raise telethon_errors.FloodWaitError(request=None, capture=1)

    with pytest.raises(telethon_errors.FloodWaitError) as raised:
        await clone_cmd._with_cooldown(short_flood, clone_state, budget)

    assert raised.value.seconds == 1
    assert no_sleep == []
    assert budget.spent == 180


@pytest.mark.asyncio
async def test_siblings_issue_no_rpcs_while_one_worker_sleeps_a_flood(
    clone_state, monkeypatch, budget
):
    """One shared FloodWait must stall every sibling worker, not only the one
    that caught it: siblings entering _with_cooldown during the wait window
    park on the shared gate instead of sending RPCs (ADR-0045/0052)."""
    real_sleep = asyncio.sleep
    holder_sleeping = asyncio.Event()
    release_holder = asyncio.Event()
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        holder_sleeping.set()
        await release_holder.wait()

    monkeypatch.setattr("tgcli.clone.cooldown.asyncio.sleep", fake_sleep)

    flood_attempts = 0

    async def flood_then_ok():
        nonlocal flood_attempts
        flood_attempts += 1
        if flood_attempts == 1:
            raise telethon_errors.FloodWaitError(request=None, capture=3)
        return "holder"

    sibling_calls = 0

    async def sibling():
        nonlocal sibling_calls
        sibling_calls += 1
        return "ok"

    holder = asyncio.ensure_future(
        clone_cmd._with_cooldown(flood_then_ok, clone_state, budget)
    )
    await holder_sleeping.wait()
    siblings = [
        asyncio.ensure_future(clone_cmd._with_cooldown(sibling, clone_state, budget))
        for _ in range(3)
    ]
    for _ in range(10):
        await real_sleep(0)
    assert sibling_calls == 0
    release_holder.set()
    assert await holder == "holder"
    assert await asyncio.gather(*siblings) == ["ok", "ok", "ok"]
    assert sibling_calls == 3
    assert sleeps == [4]
    assert budget.spent == 4


@pytest.mark.asyncio
async def test_one_flood_wait_charges_the_shared_budget_once(
    clone_state, monkeypatch, budget
):
    """Four in-flight workers all catching the same short FloodWait must not
    each sleep it out and deduct it from the one per-process budget: the
    first caller pays, the released siblings retry for free (ADR-0052)."""
    real_sleep = asyncio.sleep
    release_holder = asyncio.Event()
    sleeps: list[float] = []

    async def fake_sleep(seconds):
        sleeps.append(seconds)
        await release_holder.wait()

    monkeypatch.setattr("tgcli.clone.cooldown.asyncio.sleep", fake_sleep)

    attempts = dict.fromkeys(range(4), 0)
    in_flight = [asyncio.Event() for _ in range(4)]
    proceed = asyncio.Event()

    def thunk_for(index):
        async def thunk():
            attempts[index] += 1
            if attempts[index] == 1:
                in_flight[index].set()
                await proceed.wait()
                raise telethon_errors.FloodWaitError(request=None, capture=3)
            return "ok"

        return thunk

    workers = [
        asyncio.ensure_future(
            clone_cmd._with_cooldown(thunk_for(index), clone_state, budget)
        )
        for index in range(4)
    ]
    for event in in_flight:
        await event.wait()
    proceed.set()
    while not sleeps:
        await real_sleep(0)
    release_holder.set()
    assert await asyncio.gather(*workers) == ["ok"] * 4
    assert sleeps == [4]
    assert budget.spent == 4
    assert all(count == 2 for count in attempts.values())


@pytest.mark.asyncio
async def test_gate_reopens_after_the_flood_wait(clone_state, no_sleep, budget):
    attempts = 0

    async def once_then_ok():
        nonlocal attempts
        attempts += 1
        if attempts == 1:
            raise telethon_errors.FloodWaitError(request=None, capture=3)
        return "ok"

    assert await clone_cmd._with_cooldown(once_then_ok, clone_state, budget) == "ok"

    async def plain():
        return "next"

    result = await asyncio.wait_for(
        clone_cmd._with_cooldown(plain, clone_state, budget), timeout=1
    )
    assert result == "next"


@pytest.mark.asyncio
async def test_wait_budget_is_per_invocation_not_persisted(clone_state, no_sleep):
    async def once_then_ok():
        if not hasattr(once_then_ok, "n"):
            once_then_ok.n = 0
        once_then_ok.n += 1
        if once_then_ok.n == 1:
            raise telethon_errors.FloodWaitError(request=None, capture=3)
        return "ok"

    first = flood.WaitBudget()
    assert await clone_cmd._with_cooldown(once_then_ok, clone_state, first) == "ok"
    assert first.spent == 4

    once_then_ok.n = 0
    second = flood.WaitBudget()
    assert await clone_cmd._with_cooldown(once_then_ok, clone_state, second) == "ok"
    assert second.spent == 4
    for path in state.clones_dir().rglob("*"):
        if not path.is_file():
            continue
        # Clone state is SQLite/WAL (ADR-0060); wait-budget must not appear in
        # any text state either (account flood JSON, etc.).
        if path.suffix == ".db" or path.name.endswith((".db-wal", ".db-shm")):
            continue
        text = path.read_text()
        assert '"spent"' not in text
        assert "wait_budget" not in text
