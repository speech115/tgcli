"""The governed ``_call`` wrapper: refuse locally, arm from the server.

Two things happen around every Telegram request, and the order matters:

* **before dispatch** — if this request type is cooling, refuse here. No RPC
  leaves. The incident's cost was not one flood but an agent retrying into a
  live penalty, so the cheapest correct behaviour is to never send at all.
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

import types
from datetime import UTC, datetime, timedelta
from math import ceil

from telethon import errors as telethon_errors

from tgcli.errors import RateLimitError
from tgcli.governor import probe, registry
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


def install(client, ledger: Ledger, *, account_source=None) -> None:
    """Wrap this client's ``_call`` so every request passes the gate.

    ``account_source`` overrides where the account id comes from. A CDN client
    is constructed fresh by Telethon and has no ``_self_id``, so it is given
    its parent's — otherwise it would look like unauthenticated traffic and
    skip the gate entirely.
    """
    original = client._call
    resolve = (lambda: account_id(client)) if account_source is None else account_source

    async def governed(self, sender, request, *args, **kwargs):
        account = resolve()
        key = registry.request_key(request)
        is_probe = False
        if account is not None:
            is_probe = refuse_if_cooling(ledger, account, key)
        try:
            result = await original(sender, request, *args, **kwargs)
        except ARMING_ERRORS as exc:
            if account is not None:
                arm_from_flood(ledger, account, key, exc)
            raise
        if is_probe and account is not None:
            probe.settle(ledger, account, key)
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
    """Record the server's own deadline for the type that drew the flood."""
    seconds = getattr(exc, "seconds", None)
    if not isinstance(seconds, int | float) or seconds <= 0:
        return
    deadline = datetime.now(UTC) + timedelta(seconds=float(seconds))
    ledger.arm_cooldown(account, request_key, deadline)
