"""Opaque versioned common update cursor for archive sync.

Pure functions only, no I/O. A cursor written when archive sync still polled
channels one by one carries a ``channels`` map; it decodes, and the map is
dropped.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass

from tgcli.errors import PolicyError

CURSOR_PREFIX = "v1:"


# The stored cursor is unreadable only if someone edited the database.
REPAIR = "manual repair is required"


@dataclass(frozen=True)
class ChangesCursor:
    pts: int
    qts: int
    date: int  # unix seconds
    seq: int


def _payload(cursor: ChangesCursor) -> dict:
    return {
        "pts": cursor.pts,
        "qts": cursor.qts,
        "date": cursor.date,
        "seq": cursor.seq,
    }


def encode(cursor: ChangesCursor) -> str:
    raw = json.dumps(_payload(cursor), separators=(",", ":"), sort_keys=True).encode()
    return CURSOR_PREFIX + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode(text: str) -> ChangesCursor:
    if not isinstance(text, str) or not text:
        raise PolicyError(f"changes cursor is required; {REPAIR}")
    if not text.startswith(CURSOR_PREFIX):
        raise PolicyError(f"changes cursor is missing or unsupported; {REPAIR}")
    padded = text[len(CURSOR_PREFIX) :]
    pad = "=" * (-len(padded) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded + pad)
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PolicyError(f"changes cursor is corrupt; {REPAIR}") from exc
    if not isinstance(payload, dict):
        raise PolicyError(f"changes cursor is corrupt; {REPAIR}")
    try:
        pts = _nonneg_int(payload["pts"], "pts")
        qts = _nonneg_int(payload["qts"], "qts")
        date = _nonneg_int(payload["date"], "date")
        seq = _nonneg_int(payload["seq"], "seq")
    except KeyError as exc:
        raise PolicyError(f"changes cursor is corrupt; {REPAIR}") from exc
    return ChangesCursor(pts=pts, qts=qts, date=date, seq=seq)


def _nonneg_int(value, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PolicyError(f"changes cursor has invalid {label}; {REPAIR}")
    return value
