"""The clone flood gate: cooldown enforcement and the RPC seam (ADR-0045/0052).

Every clone command refuses to start while a cooldown is armed. Requests
themselves go through the governed ``_call`` seam (ADR-0072): a flood arms
the account-wide per-type cooldown and the command exits 5 locally; the
foreground short-wait retry (``with_cooldown``) is retired in favour of
the governor's own pacing and the explicit ``--max-runtime`` wall-clock
cap.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from math import ceil

from telethon.tl import functions

from tgcli.clone import flood, state
from tgcli.errors import RateLimitError


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


async def mutate(tg, request, clone_state: state.CloneState):
    """Send one mutation through the governed seam; record channel creation.

    Floods are handled by the governor's ``_call`` wrapper: it arms the
    per-type cooldown from the server's own ``retry_after`` and the command
    exits 5 locally on the next attempt (ADR-0072).
    """
    result = await tg(request)
    if isinstance(request, functions.channels.CreateChannelRequest):
        flood.record_peer_created(clone_state.account_user_id, datetime.now(UTC))
    return result
