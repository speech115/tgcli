"""Sleep-before-dispatch pacing and the rolling breadth budget.

ADR-0072 decision 3, plan phase 4. The incident ran wholly unpaced:
Telethon's own throttle only arms above a caller's ``limit > 3000``, so
``--limit 1000`` never engaged it and tgcli added none of its own. This
module is the gap the incident found: a persisted, cross-process minimum
interval per request type, enforced *before* dispatch.

The interval is **start-to-start** (ADR-0072 decision 3): the reservation
stamps the moment the request will actually leave, taken before the RPC
goes out. Stamping on return would turn a 3 s interval into ~4.8 s once a
typical 1.8 s request latency is added — the #140 canary measured exactly
that, which is why the ADR now says it out loud.
"""

from __future__ import annotations

import asyncio
import time

from telethon import utils as telethon_utils

from tgcli.governor import registry
from tgcli.governor.ledger import Ledger

# Process-wide governed sleep and the optional wall-clock cap (plan phase 5).
# The deadline asks pacing how much it has slept so `--timeout` can count
# only ungoverned time; the wall-clock cap is the explicit `--max-runtime`
# that bounds a long run including its sleeps.
_slept_seconds = 0.0
_wall_clock_cap: float | None = None
_wall_clock_started = 0.0

# Per-invocation accounting for the journal (plan phase 6): how many requests
# were governed, how long the run deliberately slept, and the provenance of
# the last flood-family stop.
_request_count = 0
_last_stop: dict | None = None


def reset_runtime(*, cap: float | None = None) -> None:
    """Start a fresh invocation: zero the counters, set the cap."""
    global _slept_seconds, _wall_clock_cap, _wall_clock_started
    global _request_count, _last_stop
    _slept_seconds = 0.0
    _wall_clock_cap = cap
    _wall_clock_started = time.monotonic()
    _request_count = 0
    _last_stop = None


def total_governed_sleep() -> float:
    """Seconds the governor has deliberately slept in this process so far."""
    return _slept_seconds


def _note_sleep(seconds: float) -> None:
    global _slept_seconds
    _slept_seconds += seconds


def note_request() -> None:
    """Count one governed request (called from the seam before dispatch)."""
    global _request_count
    _request_count += 1


def request_count() -> int:
    return _request_count


def note_stop(**fields) -> None:
    """Record why this invocation stopped in the flood family, if it did.

    Journal fields (plan phase 6): ``retry_after``, ``request_type`` and
    ``provenance`` when a flood or refusal ended the run; ``stop_reason``
    when a normal stop did. Last one wins — the run ends with its final
    cause.
    """
    global _last_stop
    _last_stop = fields


def last_stop() -> dict | None:
    return _last_stop


def wall_clock_remaining() -> float | None:
    """Seconds left under `--max-runtime`, or ``None`` when no cap is set."""
    if _wall_clock_cap is None:
        return None
    return _wall_clock_cap - (time.monotonic() - _wall_clock_started)


async def sleep_flood(seconds: float, *, sleep=asyncio.sleep) -> bool:
    """Sleep a FloodWait out if it fits the remaining wall-clock cap.

    Returns ``True`` when the caller may retry; ``False`` when the wait
    cannot fit — the caller exits 5 immediately without sleeping at all,
    not even partially (ADR-0072 decision 6, plan phase 5).
    """
    remaining = wall_clock_remaining()
    if remaining is None or seconds > remaining:
        return False
    # Write-ahead (review fix C1): count the sleep as governed *before* it
    # starts, so the deadline discounts it even while it is still in
    # flight. A flood-sleep longer than the remaining `--timeout` must not
    # be killed for doing exactly what the governor decided.
    _note_sleep(seconds)
    await sleep(seconds)
    return True


def _charge(request: object) -> float | None:
    """The interval this request owes, after per-unit adjustment.

    ``None`` means the class is unpaced: no pre-emptive interval, still
    fully gated on its cooldown. BY_ID pays one interval per request (the
    "10 s per 300 ids" default covers a batch; commands chunk at
    ``BY_ID_BATCH`` themselves, and a 600-id call must not pay two gaps).
    MEDIA pays per *file*, not per chunk: a large file is many
    ``upload.GetFileRequest`` calls, and the first chunk (offset 0) of a
    file is what opens the gap. Upload parts (``SaveFilePartRequest`` /
    ``SaveBigFilePartRequest``) have no ``offset`` — they are one file, so
    they owe nothing pre-emptively; their floods still gate the type.
    """
    request_class = registry.classify(request)
    interval = registry.INTERVALS[request_class]
    if interval is None:
        return None
    if request_class is registry.RequestClass.MEDIA:
        offset = getattr(request, "offset", None)
        if offset is None or offset != 0:
            return None
    return interval


async def pace_before_dispatch(
    ledger: Ledger,
    account: int,
    request: object,
    *,
    now: float | None = None,
    sleep=asyncio.sleep,
) -> None:
    """Sleep until the start-to-start floor holds, then reserve.

    The reservation lands *before* the request leaves, at the moment the
    request will actually be dispatched. Where the request's own latency
    already exceeds the interval, no sleep is owed — the interval is a
    floor on spacing, not an added delay.
    """
    interval = _charge(request)
    if interval is None:
        return
    key = registry.request_key(request)
    moment = time.time() if now is None else now
    ledger.clamp_reservation(account, key, moment)
    last = ledger.last_reserved(account, key)
    wait = interval - (moment - last) if last is not None else 0.0
    if wait > 0:
        _note_sleep(wait)  # write-ahead (review fix C1)
        await sleep(wait)
        moment += wait
    ledger.reserve(account, key, moment)


def touch_history_peer(
    ledger: Ledger, account: int, request: object, *, now: float | None = None
) -> None:
    """Record a history read's peer against the breadth budget.

    Called before dispatch; durable per peer (ADR-0072 decision 5) so a
    killed process does not hand back budget for peers it really read.
    A request without a peer (search-global, difference) is not a breadth
    read and touches nothing.
    """
    if registry.classify(request) is not registry.RequestClass.HISTORY:
        return
    peer = getattr(request, "peer", None)
    if peer is None:
        return
    try:
        peer_id = telethon_utils.get_peer_id(peer)
    except (TypeError, ValueError):
        return
    moment = time.time() if now is None else now
    ledger.touch_peer(account, peer_id, moment)


def budget_ok(ledger: Ledger, account: int, *, now: float | None = None) -> bool:
    """Whether starting work on a *new* peer fits the rolling budget.

    Commands that walk peers call this before each new one and stop
    *normally* (exit 0, ``stop_reason``, checkpoint intact) when it
    returns False.
    """
    moment = time.time() if now is None else now
    return ledger.breadth_remaining(account, moment) > 0


def governor_of(tg) -> tuple[Ledger, int] | None:
    """The ledger and account id attached to this client, if governed.

    Commands reach the governor through the client the session built for
    them: the seam installs ``_tgcli_governor`` and Telethon restores
    ``_self_id`` at connect, so no RPC is needed and no second code path
    exists to drift.
    """
    ledger = getattr(tg, "_tgcli_governor", None)
    account = getattr(tg, "_self_id", None)
    if ledger is None or not isinstance(account, int):
        return None
    return ledger, account
