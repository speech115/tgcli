"""The clone flood gate: cooldown enforcement and the RPC seam (ADR-0045/0052).

Every clone command reaches Telegram through `with_cooldown`, and every one of
them refuses to start while a cooldown is armed. That made this block the most
cross-cutting piece of the clone command surface — it belongs beside the flood
record it guards, not inside the command module, so `init`, `sync`, and
`refresh` can be read (and later split) without carrying it along.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from math import ceil

from telethon import errors as telethon_errors
from telethon.tl import functions

from tgcli.clone import flood, state
from tgcli.errors import RateLimitError
from tgcli.output import note


def raise_if_cooling(deadline: datetime) -> None:
    retry_after = ceil((deadline - datetime.now(UTC)).total_seconds())
    if retry_after > 0:
        raise RateLimitError(
            f"rate limited for {retry_after}s", retry_after=retry_after
        )


def enforce_account(account_user_id: int) -> None:
    deadline = flood.cooldown_deadline(account_user_id)
    if deadline is not None:
        raise_if_cooling(deadline)


def session_account_id(tg) -> int | None:
    """The logged-in account id already known to the session — no RPC.

    Telethon restores it in ``connect()`` from the session's own self-user row,
    which is what lets the account-scoped cooldown record be selected before any
    Telegram traffic (CONTRACT §11: exit 5 locally, no network). None when a
    session never cached it; the caller then falls back to the get_me RPC.
    """
    account_id = getattr(tg, "_self_id", None)
    return account_id if isinstance(account_id, int) else None


async def cooled_account(tg):
    """Resolve the account for a run whose cooldown gate must come first."""
    if (account_id := session_account_id(tg)) is not None:
        enforce_account(account_id)
    me = await tg.get_me()
    enforce_account(me.id)
    return me


def enforce(clone_state: state.CloneState) -> None:
    deadlines = [
        deadline
        for deadline in (
            clone_state.cooldown_deadline(),
            flood.cooldown_deadline(clone_state.account_user_id),
        )
        if deadline is not None
    ]
    if deadlines:
        raise_if_cooling(max(deadlines))


def arm_account(account_user_id: int, seconds: int) -> None:
    """Arm the per-account flood record (no clone-state dependency)."""
    deadline = datetime.now(UTC) + timedelta(seconds=seconds)
    flood.arm_cooldown(account_user_id, deadline)


def arm(clone_state: state.CloneState, seconds: int) -> None:
    deadline = datetime.now(UTC) + timedelta(seconds=seconds)
    clone_state.set_cooldown(deadline)
    state.save(clone_state)
    flood.arm_cooldown(clone_state.account_user_id, deadline)


async def with_cooldown(make_awaitable, clone_state, budget: flood.WaitBudget):
    """Run ``make_awaitable()`` under FloodWait cooldown arming.

    ``make_awaitable`` is a zero-arg callable that builds a fresh awaitable —
    a coroutine object cannot be re-awaited (ADR-0052 task 1). A short
    ``FloodWaitError`` (≤ ``flood.SHORT_WAIT``) is waited out once when the
    per-process ``budget`` still has room, then the thunk is retried; a second
    failure, a longer wait, or a spent budget raises after arming both
    cooldowns (ADR-0052 / ADR-0045). Concurrent callers (parallel upload
    workers) share ``budget.gate``: the caller sleeping a short wait out
    holds the gate, and every sibling parks before its next attempt instead
    of issuing RPCs, charging the budget, or sleeping the same wait again.
    """
    gate = budget.gate
    slept = False
    while True:
        await gate.wait()
        try:
            return await make_awaitable()
        except telethon_errors.FloodWaitError as exc:
            arm(clone_state, exc.seconds)
            if exc.seconds > flood.SHORT_WAIT:
                raise
            if gate.held:
                # A sibling already sleeps this wait out; park on the gate
                # and retry without paying for a wait this caller never
                # sleeps. held-check → hold() below is synchronous-only, so
                # no task can slip in between (see flood.FloodGate).
                continue
            if slept or not budget.try_spend(exc.seconds + 1):
                raise
            slept = True
            # note() before hold(): a stderr write failure (closed pipe) must
            # not leave the gate held. Both are synchronous, so the
            # held-check → hold() atomicity above still stands.
            note(f"flood wait: retrying in {exc.seconds}s")
            gate.hold()
            try:
                await asyncio.sleep(exc.seconds + 1)
            finally:
                gate.release()


async def mutate(tg, request, clone_state: state.CloneState, budget: flood.WaitBudget):
    result = await with_cooldown(lambda: tg(request), clone_state, budget)
    if isinstance(request, functions.channels.CreateChannelRequest):
        flood.record_peer_created(clone_state.account_user_id, datetime.now(UTC))
    return result
