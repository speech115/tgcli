"""Shared client-side throttle for contacts.resolvePhone."""

import fcntl
import math
import time
from pathlib import Path

from tgcli import atomic
from tgcli.errors import RateLimitError
from tgcli.governor import pacing
from tgcli.state_paths import ensure_state_dir

# Telegram documents roughly ≤1 resolvePhone call per ~3 seconds client-side.
RESOLVE_PHONE_COOLDOWN_S = 3.0
# The governor's in-process pace is timed on the monotonic clock and this
# throttle on the wall clock; a paced call must not fail on their drift.
CLOCK_SLACK_S = 0.05


def _cooldown_path() -> Path:
    return ensure_state_dir() / "resolve-phone.cooldown"


def _cooldown_lock_path() -> Path:
    return ensure_state_dir() / "resolve-phone.cooldown.lock"


def enforce_resolve_phone_cooldown(*, now: float | None = None) -> None:
    """Raise RateLimitError if another resolvePhone ran too recently.

    Persists last-call wall time under the state dir so successive short-lived
    `tg` processes share the throttle (not just in-process loops).
    """
    moment = time.time() if now is None else now
    path = _cooldown_path()
    with _cooldown_lock_path().open("a+") as lock_file:
        fcntl.flock(lock_file, fcntl.LOCK_EX)
        if path.exists():
            try:
                last = float(path.read_text().strip())
            except (OSError, ValueError):
                last = 0.0
            if last > moment:
                # The wall clock was stepped back (NTP/DST/manual) after the
                # reservation was written: waiting out the whole jump would
                # wedge every resolvePhone until the clock catches up again.
                # Clamp under the lock so it degrades to one cooldown.
                last = moment
                atomic.replace_text(path, f"{last}\n")
            wait = RESOLVE_PHONE_COOLDOWN_S - (moment - last)
            if wait > CLOCK_SLACK_S:
                retry_after = max(1, math.ceil(wait))
                pacing.note_stop(
                    retry_after=retry_after,
                    request_type="contacts.ResolvePhoneRequest",
                    provenance="resolve_phone_cooldown",
                )
                raise RateLimitError(
                    f"contacts.resolvePhone cooldown: retry after {retry_after}s",
                    retry_after=retry_after,
                )
        atomic.replace_text(path, f"{moment}\n")
