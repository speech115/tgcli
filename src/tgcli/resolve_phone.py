"""Shared client-side throttle for contacts.resolvePhone."""

import math
import time
from pathlib import Path

from tgcli.errors import RateLimitError
from tgcli.session import state_dir


# Telegram documents roughly ≤1 resolvePhone call per ~3 seconds client-side.
RESOLVE_PHONE_COOLDOWN_S = 3.0


def _cooldown_path() -> Path:
    return state_dir() / "resolve-phone.cooldown"


def enforce_resolve_phone_cooldown(*, now: float | None = None) -> None:
    """Raise RateLimitError if another resolvePhone ran too recently.

    Persists last-call wall time under the state dir so successive short-lived
    `tg` processes share the throttle (not just in-process loops).
    """
    moment = time.time() if now is None else now
    path = _cooldown_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        try:
            last = float(path.read_text().strip())
        except (OSError, ValueError):
            last = 0.0
        wait = RESOLVE_PHONE_COOLDOWN_S - (moment - last)
        if wait > 0:
            retry_after = max(1, math.ceil(wait))
            raise RateLimitError(
                f"contacts.resolvePhone cooldown: retry after {retry_after}s",
                retry_after=retry_after,
            )
    path.write_text(f"{moment}\n")
