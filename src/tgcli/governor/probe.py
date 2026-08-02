"""The self-verifying probe (ADR-0072 decision 1, plan phase 3).

A recorded cooldown deadline is a guess about the future: Telegram may lift
a limit early, or extend it. The probe is one request, sent once per
*confirmed* deadline, that asks the server what is actually true.

This is not a retry. A failed probe rewrites the record from the server's
answer and nothing else is sent; because ``arm_cooldown`` resets
``probe_spent``, a fresh deadline earns a fresh probe, bounding the cost at
one request per confirmed deadline rather than a loop.

Once phase 4 lands, the probe is paced by the same seam as any other
request — the start-to-start reservation happens around ``_call`` — so no
pacing is implemented here.
"""

from __future__ import annotations

from datetime import UTC, datetime

from tgcli.governor.ledger import Ledger

# ADR-0072 decision 3: the probe fires once 50% of a recorded wait has
# elapsed.
PROBE_FRACTION = 0.5


def probe_due(
    ledger: Ledger,
    account: int,
    request_key: str,
    *,
    now: datetime | None = None,
) -> bool:
    """Whether the probe window is open: 50% elapsed and the probe unspent.

    An unparseable ``armed_at`` (hand-edited row) reads as "not yet 50%" —
    the safe direction is to refuse rather than to send into a live
    penalty.
    """
    if ledger.probe_spent(account, request_key):
        return False
    moment = datetime.now(UTC) if now is None else now
    deadline = ledger.cooldown_deadline(account, request_key, now=moment)
    armed_at = ledger.cooldown_armed_at(account, request_key)
    if deadline is None or armed_at is None:
        return False
    wait = (deadline - armed_at).total_seconds()
    if wait <= 0:
        return False
    return (moment - armed_at).total_seconds() >= PROBE_FRACTION * wait


def claim_if_due(
    ledger: Ledger,
    account: int,
    request_key: str,
    *,
    now: datetime | None = None,
) -> bool:
    """Atomically claim the probe right when the window is open.

    ``True`` means the caller may send this request as the probe: the spend
    landed *before* the request leaves (ADR-0072 decision 5), so a crash
    between claim and send leaves the probe spent. ``False`` means refuse
    normally without sending — the window is closed, or another process
    already claimed the probe.
    """
    if not probe_due(ledger, account, request_key, now=now):
        return False
    return ledger.spend_probe(account, request_key)


def settle(ledger: Ledger, account: int, request_key: str) -> None:
    """A probe that came back clean drops the record it was testing."""
    ledger.clear_cooldown(account, request_key)
