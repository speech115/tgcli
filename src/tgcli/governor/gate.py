"""The governed ``_call`` wrapper: refuse locally, arm from the server.

Two things happen around every Telegram request, and the order matters:

* **before dispatch** — if this request type is cooling, refuse here. No RPC
  leaves. The incident's cost was not one flood but an agent retrying into a
  live penalty, so the cheapest correct behaviour is to never send at all.
* **after a flood** — arm the cooldown for exactly the type that drew it, from
  the server's own ``retry_after``. Never a guess, never a peer.

Installed on the *instance*, not the class. ``downloads.py`` and the
CDN-redirect path call ``_call`` directly on a per-datacentre sender; an
instance attribute still shadows the class method for those calls, which is the
reason ADR-0072 decision 2 rejected the public ``__call__`` as the seam.
"""

from __future__ import annotations

import types
from datetime import UTC, datetime, timedelta
from math import ceil

from telethon import errors as telethon_errors

from tgcli.errors import RateLimitError
from tgcli.governor import registry
from tgcli.governor.ledger import Ledger


def account_id(client) -> int | None:
    """The logged-in account id the session already knows — no RPC.

    Telethon restores it during ``connect()``. ``None`` before that, which is
    exactly the window where the only traffic is connection and authorization:
    governing those would make a cooling account impossible to log into.
    """
    value = getattr(client, "_self_id", None)
    return value if isinstance(value, int) else None


def install(client, ledger: Ledger) -> None:
    """Wrap this client's ``_call`` so every request passes the gate."""
    original = client._call

    async def governed(self, sender, request, *args, **kwargs):
        account = account_id(self)
        key = registry.request_key(request)
        if account is not None:
            refuse_if_cooling(ledger, account, key)
        try:
            return await original(sender, request, *args, **kwargs)
        except telethon_errors.FloodWaitError as exc:
            if account is not None:
                arm_from_flood(ledger, account, key, exc)
            raise

    client._call = types.MethodType(governed, client)
    client._tgcli_governor = ledger


def refuse_if_cooling(ledger: Ledger, account: int, request_key: str) -> None:
    """Raise before any RPC when this request type is still cooling."""
    deadline = ledger.cooldown_deadline(account, request_key)
    if deadline is None:
        return
    retry_after = ceil((deadline - datetime.now(UTC)).total_seconds())
    if retry_after <= 0:
        return
    raise RateLimitError(
        f"{request_key} is rate limited for {retry_after}s",
        retry_after=retry_after,
    )


def arm_from_flood(
    ledger: Ledger,
    account: int,
    request_key: str,
    exc: telethon_errors.FloodWaitError,
) -> None:
    """Record the server's own deadline for the type that drew the flood."""
    seconds = getattr(exc, "seconds", None)
    if not isinstance(seconds, int | float) or seconds <= 0:
        return
    deadline = datetime.now(UTC) + timedelta(seconds=float(seconds))
    ledger.arm_cooldown(account, request_key, deadline)
