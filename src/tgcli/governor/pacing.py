"""Request pacing inside one process, governed sleep, and the wall-clock cap.

The 2026-07-31 incident (ADR-0072) was one process walking hundreds of dialogs
with no spacing at all, which drew a 21.5-hour FloodWait. A single command
sends a handful of requests and needs no spacing, so each request type gets
``FREE_REQUESTS`` per process; past that, its interval applies start-to-start
before every request. Loops (backfill, clone, export, bulk media, scripts)
are paced; interactive commands are not.
"""

from __future__ import annotations

import asyncio
import time

from tgcli.governor import registry

# Requests of one type a process may send before pacing starts.
FREE_REQUESTS = 5

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
# Per request type: how many were sent and when the last one left.
_sent: dict[str, int] = {}
_last_sent: dict[str, float] = {}


def reset_runtime(*, cap: float | None = None) -> None:
    """Start a fresh invocation: zero the counters, set the cap."""
    global _slept_seconds, _wall_clock_cap, _wall_clock_started
    global _request_count, _last_stop
    _slept_seconds = 0.0
    _wall_clock_cap = cap
    _wall_clock_started = time.monotonic()
    _request_count = 0
    _last_stop = None
    _sent.clear()
    _last_sent.clear()


def total_governed_sleep() -> float:
    """Seconds the governor has deliberately slept in this process so far."""
    return _slept_seconds


def _note_sleep(seconds: float) -> None:
    global _slept_seconds
    _slept_seconds += seconds


def sleep_governed(seconds: float) -> None:
    """Block for ``seconds`` as deliberate waiting `--timeout` does not count."""
    _note_sleep(seconds)
    time.sleep(seconds)


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


def wall_clock_exhausted() -> bool:
    """Whether `--max-runtime` was set and has run out."""
    remaining = wall_clock_remaining()
    return remaining is not None and remaining <= 0


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
    request: object, *, sleep=asyncio.sleep, clock=time.monotonic
) -> None:
    """Hold the start-to-start interval once this type used its free requests."""
    interval = _charge(request)
    if interval is None:
        return
    key = registry.request_key(request)
    _sent[key] = _sent.get(key, 0) + 1
    free = 0 if registry.classify(request) in registry.ALWAYS_PACED else FREE_REQUESTS
    now = clock()
    start = now
    last = _last_sent.get(key)
    if _sent[key] > free and last is not None:
        start = max(now, last + interval)
    # Reserve the slot before sleeping, so concurrent tasks in this process
    # queue behind it instead of computing the same wait.
    _last_sent[key] = start
    if start > now:
        _note_sleep(start - now)  # write-ahead: --timeout must not count it
        await sleep(start - now)
