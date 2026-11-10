"""How tgcli classifies Telegram errors: a flood, "ask again later", or the run.

Every catch site that maps an error to a lasting outcome uses these tuples,
so the classification changes in one place.
"""

from __future__ import annotations

from telethon import errors as telethon_errors

from tgcli.errors import RateLimitError

# Telegram's 420 families that say "wait N seconds" about the account, not the
# request: FLOOD_WAIT_X, and FLOOD_PREMIUM_WAIT_X, the transfer speed limit a
# non-Premium account meets. Both carry `.seconds`, both arm the governor, and
# both exit 5. Catch a flood through this tuple, never through one class.
FLOOD_ERRORS = (
    telethon_errors.FloodWaitError,
    telethon_errors.FloodPremiumWaitError,
)

# "Ask again later", never "Telegram refused". `raise_last_call_error=True`
# makes a call whose retries ran out raise its own error; without it Telethon
# raises ValueError, the same type `get_entity` uses for "no such peer", and
# callers turned one 500 into permanent state. Code that maps refusals to a
# lasting outcome (a marker, a fallback, a cached miss) re-raises these first.
TRANSIENT_ERRORS = (
    *FLOOD_ERRORS,
    telethon_errors.ServerError,
    telethon_errors.TimedOutError,
    # Telethon's own reconnects ran out: the network, not the request.
    ConnectionError,
)

# Errors about the run, never about the one item it was working on: a flood
# or a governor cooldown, a connection Telethon could not restore, a session
# Telegram no longer accepts. A loop that records a per-item failure (an
# attempt counter, an `error` field, a skipped peer) re-raises these before
# its `except Exception`, or one outage marks every item it touched.
RUN_ABORTING_ERRORS = (
    *FLOOD_ERRORS,
    RateLimitError,
    ConnectionError,
    telethon_errors.UnauthorizedError,
)


async def unless_refused(awaitable):
    """``(result, None)``, or ``(None, refusal)`` when Telegram said no.

    A refusal is a lasting answer about this peer or request: Telethon's
    ValueError for a peer it cannot resolve, or any RPCError that is not in
    TRANSIENT_ERRORS. Transient errors propagate, so a flood still arms the
    governor and a 500 is never cached as a permanent miss. Callers map the
    refusal to their own lasting outcome (a marker, a fallback, a degrade).
    """
    try:
        return await awaitable, None
    except TRANSIENT_ERRORS:
        raise
    except (ValueError, telethon_errors.RPCError) as exc:
        return None, exc
