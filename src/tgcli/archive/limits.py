"""Every numeric bound of `tg archive`, and the one check that applies them.

`archive/preflight.py` normalizes each bounded flag through `bounded` before
any command runs, so the commands themselves take plain ints.
"""

from __future__ import annotations

from typing import NamedTuple

from tgcli.errors import PolicyError


class Bound(NamedTuple):
    default: int
    maximum: int
    unit: str = ""


BOUNDS: dict[tuple[str, str], Bound] = {
    ("backfill", "--limit"): Bound(100, 1000, "messages per dialog"),
    ("backfill", "--max-dialogs"): Bound(20, 100, "dialogs"),
    ("sync", "--max-events"): Bound(500, 5000),
    ("sync", "--max-dialogs"): Bound(20, 50),
    ("sync", "--max-media"): Bound(50, 500),
    ("search", "--limit"): Bound(20, 50, "hits"),
    ("search", "--page"): Bound(1, 10_000),
    ("read", "--limit"): Bound(20, 50, "hits"),
    ("transcribe", "--limit"): Bound(20, 100),
    ("transcribe", "--max-attempts"): Bound(3, 5),
}
# CHAT arguments one `archive backfill` accepts (a count, not a flag value).
BACKFILL_MAX_CHATS = 20


def default(command: str, flag: str) -> int:
    return BOUNDS[command, flag].default


def bounded(command: str, flag: str, value: int | None) -> int:
    """Return the flag's default for None, else the value if within its bound."""
    bound = BOUNDS[command, flag]
    if value is None:
        return bound.default
    if value <= 0:
        raise PolicyError(f"archive {command} {flag} must be positive")
    if value > bound.maximum:
        unit = f" {bound.unit}" if bound.unit else ""
        raise PolicyError(
            f"archive {command} {flag} accepts at most {bound.maximum}{unit}"
        )
    return value
