"""Cooldown seam thunk conversion (ADR-0052 task 1) — behaviour-preserving."""

from datetime import UTC, datetime

import pytest
from telethon import errors as telethon_errors

from tgcli.clone import state
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


@pytest.mark.asyncio
async def test_with_cooldown_raises_on_first_flood_without_retry(clone_state):
    """Task 1 must not introduce retry: a FloodWait still raises immediately."""
    attempts = 0

    async def boom():
        nonlocal attempts
        attempts += 1
        raise telethon_errors.FloodWaitError(request=None, capture=3)

    with pytest.raises(telethon_errors.FloodWaitError) as raised:
        await clone_cmd._with_cooldown(boom, clone_state)

    assert raised.value.seconds == 3
    assert attempts == 1
    deadline = clone_state.cooldown_deadline()
    assert deadline is not None
    assert deadline > datetime.now(UTC)


@pytest.mark.asyncio
async def test_with_cooldown_awaits_fresh_thunk_result(clone_state):
    calls = 0

    async def ok():
        nonlocal calls
        calls += 1
        return "done"

    assert await clone_cmd._with_cooldown(ok, clone_state) == "done"
    assert calls == 1
