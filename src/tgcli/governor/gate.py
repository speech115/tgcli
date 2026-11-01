"""The governed ``_call`` wrapper: refuse locally, arm from the server.

Around every authenticated Telegram request:

* **before dispatch, cooling** — if this request type is still cooling from
  a FloodWait, sleep it out when it fits the ``--max-runtime`` cap, else
  refuse here with exit 5. No RPC leaves: the costly mistake is an agent
  retrying into a live penalty.
* **before dispatch, pacing** — past its free requests in this process, a
  type waits its start-to-start interval (``pacing``).
* **after a flood** — arm the cooldown for exactly the type that drew it,
  from the server's own ``retry_after``, then sleep it out and send the
  request once more when the wait fits the cap. A failed durable arm loses
  only the cross-process memory: Telethon itself refuses that type for the
  rest of this process.

Installed on the *instance*: ``downloads.py`` calls ``_call`` directly on a
per-datacentre sender, and an instance attribute still shadows the class
method there. Telethon's ``_get_cdn_client`` builds a brand-new client with
``self.__class__(...)``, so the factory is wrapped too and the child inherits
the parent's ledger and account.
"""

from __future__ import annotations

import asyncio
import time
import types
from datetime import UTC, datetime, timedelta
from math import ceil

from telethon import errors as telethon_errors

from tgcli.errors import RateLimitError
from tgcli.governor import pacing, registry
from tgcli.governor.ledger import Ledger

# Which flood families arm an account-wide, peer-agnostic cooldown.
# `SlowModeWaitError` is deliberately absent: Telethon's own comment marks it
# chat-specific, and ADR-0072 decision 1 keys the ledger on the request type
# with the peer excluded on purpose — arming from a per-chat limit would refuse
# every other chat for a restriction that never applied to them.
ARMING_ERRORS = (
    telethon_errors.FloodWaitError,
    telethon_errors.FloodPremiumWaitError,
)


def account_id(client) -> int | None:
    """The logged-in account id the session already knows — no RPC.

    Telethon restores it during ``connect()``. ``None`` before that, which is
    exactly the window where the only traffic is connection and authorization:
    governing those would make a cooling account impossible to log into.
    """
    value = getattr(client, "_self_id", None)
    return value if isinstance(value, int) else None


def install(
    client,
    ledger: Ledger,
    *,
    account_source=None,
    sleep=asyncio.sleep,
    clock=time.monotonic,
) -> None:
    """Wrap this client's ``_call`` so every request passes the gate.

    ``account_source`` overrides where the account id comes from. A CDN client
    is constructed fresh by Telethon and has no ``_self_id``, so it is given
    its parent's — otherwise it would look like unauthenticated traffic and
    skip the gate entirely. ``sleep`` and ``clock`` are injectable so tests
    can fake pacing instead of sleeping in real time.
    """
    original = client._call
    resolve = (lambda: account_id(client)) if account_source is None else account_source

    async def governed(self, sender, request, *args, **kwargs):
        account = resolve()
        key = registry.request_key(request)
        if account is not None:
            # ADR-0072 decision 6: a recorded wait that fits the --max-runtime
            # cap is slept out; otherwise the type refuses locally.
            wait = cooling_seconds(ledger, account, key)
            if not (wait and await pacing.sleep_flood(wait, sleep=sleep)):
                refuse_if_cooling(ledger, account, key)
            await pacing.pace_before_dispatch(
                request,
                slow_start=recently_flooded(ledger, account, key),
                sleep=sleep,
                clock=clock,
            )
            pacing.note_request()
        retried = False
        while True:
            try:
                return await original(sender, request, *args, **kwargs)
            except ARMING_ERRORS as exc:
                if account is None:
                    raise
                arm_from_flood(ledger, account, key, exc)
                pacing.note_stop(
                    retry_after=exc.seconds,
                    request_type=key,
                    provenance="server",
                )
                # Same rule for a live flood, once per request: Telegram
                # refused it unexecuted, so sending it again is safe.
                if retried or not await pacing.sleep_flood(exc.seconds, sleep=sleep):
                    raise
                retried = True

    client._call = types.MethodType(governed, client)
    _govern_cdn_children(client, ledger, resolve)


def _govern_cdn_children(client, ledger: Ledger, resolve) -> None:
    """Make Telethon's CDN client factory hand back a governed client."""
    factory = getattr(client, "_get_cdn_client", None)
    if factory is None:
        return

    async def governed_factory(self, cdn_redirect):
        cdn = await factory(cdn_redirect)
        # The child never saw `session._make_client`, so it keeps Telethon's
        # default sleeper unless we clear it here as well.
        cdn.flood_sleep_threshold = 0
        install(cdn, ledger, account_source=resolve)
        return cdn

    client._get_cdn_client = types.MethodType(governed_factory, client)


def cooling_seconds(ledger: Ledger, account: int, request_key: str) -> int:
    """Whole seconds this request type still cools, 0 when it is free."""
    deadline = ledger.cooldown_deadline(account, request_key)
    if deadline is None:
        return 0
    return max(0, ceil((deadline - datetime.now(UTC)).total_seconds()))


def recently_flooded(ledger: Ledger, account: int, request_key: str) -> bool:
    """Whether this type is cooling, or stopped cooling within the window.

    Counted from the end of the cooldown, not its arming: after a 21-hour
    flood the first run is the one that must not burst.
    """
    since = datetime.now(UTC) - timedelta(seconds=pacing.SLOW_START_WINDOW_S)
    return ledger.cooled_since(account, request_key, since)


def refuse_if_cooling(ledger: Ledger, account: int, request_key: str) -> None:
    """Refuse before any RPC while this request type is still cooling."""
    retry_after = cooling_seconds(ledger, account, request_key)
    if retry_after <= 0:
        return
    pacing.note_stop(
        retry_after=retry_after,
        request_type=request_key,
        provenance="account_cooldown",
    )
    raise RateLimitError(
        f"{request_key} is rate limited for {retry_after}s",
        retry_after=retry_after,
    )


def arm_from_flood(
    ledger: Ledger,
    account: int,
    request_key: str,
    exc: BaseException,
) -> None:
    """Record the server's own deadline for the type that drew the flood.

    A write that fails loses only the memory other processes would read:
    with ``flood_sleep_threshold=0`` Telethon already refuses the same
    request type locally for the rest of this process (ADR-0090's hole).

    The stderr line is the one alert for this cooldown: it fires at
    arming, not on every scheduled wake that finds the type still cooling
    (plan phase 6, L13). Wakes under a cooldown are silent-but-successful
    — the journal carries the refusal's provenance instead.
    """
    seconds = getattr(exc, "seconds", None)
    if not isinstance(seconds, int | float) or seconds <= 0:
        return
    deadline = datetime.now(UTC) + timedelta(seconds=float(seconds))
    from tgcli.output import note

    persisted = ledger.arm_cooldown(account, request_key, deadline)
    note(
        f"telegram flood on {request_key}: cooling for {seconds}s"
        + ("" if persisted else " (cooldown not persisted)")
    )
