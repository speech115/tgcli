"""How tgcli classifies Telegram errors: a flood, "ask again later", or the run.

Every catch site that maps an error to a lasting outcome uses these tuples,
so the classification changes in one place.
"""

from __future__ import annotations

from telethon import errors as telethon_errors

from tgcli.errors import RateLimitError

FLOOD_ERRORS = (
    telethon_errors.FloodWaitError,
    telethon_errors.FloodPremiumWaitError,
)

TRANSIENT_ERRORS = (
    *FLOOD_ERRORS,
    telethon_errors.ServerError,
    telethon_errors.TimedOutError,
    ConnectionError,
)

RUN_ABORTING_ERRORS = (
    *FLOOD_ERRORS,
    RateLimitError,
    ConnectionError,
    telethon_errors.UnauthorizedError,
)


NOT_REFUSALS = (*TRANSIENT_ERRORS, telethon_errors.UnauthorizedError)


async def unless_refused(awaitable):
    """``(result, None)``, or ``(None, refusal)`` when Telegram said no.

    A refusal is a lasting answer about this peer or request: Telethon's
    ValueError for a peer it cannot resolve, or any RPCError that is neither
    transient nor about the run. Those propagate, so a flood still exits 5, a
    500 is never cached as a permanent miss, and a dead session asks for a
    re-login instead of marking the peer. Callers map the
    refusal to their own lasting outcome (a marker, a fallback, a degrade).
    """
    try:
        return await awaitable, None
    except NOT_REFUSALS:
        raise
    except (ValueError, telethon_errors.RPCError) as exc:
        return None, exc
