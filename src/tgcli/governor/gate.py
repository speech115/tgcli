"""The governed ``_call`` wrapper: refuse locally, arm from the server.

Three things happen around every authenticated Telegram request, and the
order matters:

* **before dispatch, ledger health** — if the governor ledger is degraded
  (unopenable on disk), refuse with ``PolicyError`` (ADR-0089). Pre-auth
  traffic has no account id yet and skips this path.
* **before dispatch, cooling** — if this request type is still cooling,
  refuse here. No RPC leaves. The incident's cost was not one flood but an
  agent retrying into a live penalty, so the cheapest correct behaviour is to
  never send at all.
* **after a flood** — arm the cooldown for exactly the type that drew it, from
  the server's own ``retry_after``. Never a guess, never a peer.

Installed on the *instance*, not the class. ``downloads.py`` calls ``_call``
directly on a per-datacentre sender; an instance attribute still shadows the
class method for those calls, which is the reason ADR-0072 decision 2 rejected
the public ``__call__`` as the seam.

The CDN redirect needs more than that, and instance-patching alone does **not**
cover it. Telethon's ``_get_cdn_client`` builds a *brand-new* client with
``self.__class__(...)`` (``telegrambaseclient.py``), which never passes through
``session._make_client``: it would carry neither the wrapper nor
``flood_sleep_threshold=0``, and would fetch CDN file bytes completely
ungoverned while its own sleeper silently absorbed floods. So the factory is
wrapped too, and the child inherits the parent's ledger and account — a fresh
client has no ``_self_id`` of its own, so without that inheritance it would
land in the ungated authorization window instead.
"""

from __future__ import annotations

import asyncio
import time
import types
from datetime import UTC, datetime, timedelta
from math import ceil

from telethon import errors as telethon_errors

from tgcli.errors import PolicyError, RateLimitError
from tgcli.governor import pacing, probe, registry
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
    clock=time.time,
) -> None:
    """Wrap this client's ``_call`` so every request passes the gate.

    ``account_source`` overrides where the account id comes from. A CDN client
    is constructed fresh by Telethon and has no ``_self_id``, so it is given
    its parent's — otherwise it would look like unauthenticated traffic and
    skip the gate entirely. ``sleep`` and ``clock`` are injectable so tests
    can fake time instead of sleeping in real time.
    """
    original = client._call
    resolve = (lambda: account_id(client)) if account_source is None else account_source

    async def governed(self, sender, request, *args, **kwargs):
        account = resolve()
        key = registry.request_key(request)
        is_probe = False
        if account is not None:
            refuse_if_degraded(ledger)
            is_probe = refuse_if_cooling(ledger, account, key)
            if not is_probe:
                # The probe must not pace itself out of its own cooldown
                # window; every other request reserves start-to-start
                # *before* dispatch (ADR-0072 decision 3, plan phase 4).
                await pacing.pace_before_dispatch(
                    ledger, account, request, now=clock(), sleep=sleep
                )
            pacing.touch_history_peer(ledger, account, request, now=clock())
            pacing.note_request()
        try:
            result = await original(sender, request, *args, **kwargs)
        except ARMING_ERRORS as exc:
            if account is not None:
                arm_from_flood(ledger, account, key, exc)
                pacing.note_stop(
                    retry_after=exc.seconds,
                    request_type=key,
                    provenance="server",
                )
            raise
        if is_probe and account is not None:
            probe.settle(ledger, account, key)
            # The probe skipped the pacing reservation; stamp one now so
            # the next request paces from the probe, not from before the
            # cooldown (review fix M8).
            ledger.reserve(account, key, clock())
        return result

    client._call = types.MethodType(governed, client)
    client._tgcli_governor = ledger
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


def refuse_if_degraded(ledger: Ledger) -> None:
    """Refuse governed traffic when the ledger cannot persist protection.

    ADR-0089: a corrupt or unopenable ``governor.db`` used to fail open
    (dispatch as if nothing were cooling). That silently removes the
    account-wide hedge. Fail closed instead — exit 2 — so operators fix
    the ledger rather than burn FloodWait. Pre-auth traffic (no account
    id yet) never reaches this helper; ``doctor --connect`` skips the
    seam entirely.
    """
    if not ledger.degraded:
        return
    raise PolicyError(
        "request governor ledger is unavailable; "
        "fix or remove the governor.db under TGCLI_STATE_DIR and retry"
    )


def refuse_if_cooling(ledger: Ledger, account: int, request_key: str) -> bool:
    """Refuse before any RPC when this request type is still cooling.

    Returns ``True`` when the request may proceed *as the self-verifying
    probe* (plan phase 3): the probe was claimed write-ahead, so the caller
    settles the record on success and lets the normal flood path re-arm it
    on failure. Raises ``RateLimitError`` when the type is cooling and the
    probe must not fire.
    """
    deadline = ledger.cooldown_deadline(account, request_key)
    if deadline is None:
        return False
    retry_after = ceil((deadline - datetime.now(UTC)).total_seconds())
    if retry_after <= 0:
        return False
    if probe.claim_if_due(ledger, account, request_key):
        return True
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

    The stderr line is the one alert for this cooldown: it fires at
    arming, not on every scheduled wake that finds the type still cooling
    (plan phase 6, L13). Wakes under a cooldown are silent-but-successful
    — the journal carries the refusal's provenance instead.
    """
    seconds = getattr(exc, "seconds", None)
    if not isinstance(seconds, int | float) or seconds <= 0:
        return
    deadline = datetime.now(UTC) + timedelta(seconds=float(seconds))
    ledger.arm_cooldown(account, request_key, deadline)
    from tgcli.output import note

    note(f"telegram flood on {request_key}: cooling for {seconds}s")
