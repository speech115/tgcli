"""Opaque versioned cursor for `tg changes` (ADR-0063).

Pure functions only — no I/O. The caller owns the string between invocations.
"""

from __future__ import annotations

import base64
import json
from dataclasses import dataclass, field

from tgcli.errors import PolicyError

CURSOR_PREFIX = "v1:"


@dataclass(frozen=True)
class ChangesCursor:
    pts: int
    qts: int
    date: int  # unix seconds
    seq: int
    channels: dict[int, int] = field(default_factory=dict)
    # channels: marked peer id (-100…) → pts


def encode(cursor: ChangesCursor) -> str:
    payload = {
        "pts": cursor.pts,
        "qts": cursor.qts,
        "date": cursor.date,
        "seq": cursor.seq,
        "channels": {str(peer): pts for peer, pts in sorted(cursor.channels.items())},
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return CURSOR_PREFIX + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode(text: str) -> ChangesCursor:
    if not isinstance(text, str) or not text:
        raise PolicyError("changes cursor is required; run: tg changes --init")
    if not text.startswith(CURSOR_PREFIX):
        raise PolicyError(
            "changes cursor is missing or unsupported; run: tg changes --init"
        )
    padded = text[len(CURSOR_PREFIX) :]
    pad = "=" * (-len(padded) % 4)
    try:
        raw = base64.urlsafe_b64decode(padded + pad)
        payload = json.loads(raw.decode("utf-8"))
    except (ValueError, json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise PolicyError("changes cursor is corrupt; run: tg changes --init") from exc
    if not isinstance(payload, dict):
        raise PolicyError("changes cursor is corrupt; run: tg changes --init")
    try:
        pts = _nonneg_int(payload["pts"], "pts")
        qts = _nonneg_int(payload["qts"], "qts")
        date = _nonneg_int(payload["date"], "date")
        seq = _nonneg_int(payload["seq"], "seq")
        channels_raw = payload.get("channels", {})
        if not isinstance(channels_raw, dict):
            raise PolicyError("changes cursor is corrupt; run: tg changes --init")
        channels: dict[int, int] = {}
        for key, value in channels_raw.items():
            peer = _marked_peer(key)
            channels[peer] = _nonneg_int(value, "channel pts")
    except KeyError as exc:
        raise PolicyError("changes cursor is corrupt; run: tg changes --init") from exc
    return ChangesCursor(pts=pts, qts=qts, date=date, seq=seq, channels=channels)


def with_channel(cursor: ChangesCursor, peer: int, pts: int) -> ChangesCursor:
    channels = dict(cursor.channels)
    channels[peer] = pts
    return ChangesCursor(
        pts=cursor.pts,
        qts=cursor.qts,
        date=cursor.date,
        seq=cursor.seq,
        channels=channels,
    )


def without_channel(cursor: ChangesCursor, peer: int) -> ChangesCursor:
    if peer not in cursor.channels:
        raise PolicyError(
            f"peer {peer} is not a subscribed channel in this cursor; nothing to drop"
        )
    channels = dict(cursor.channels)
    del channels[peer]
    return ChangesCursor(
        pts=cursor.pts,
        qts=cursor.qts,
        date=cursor.date,
        seq=cursor.seq,
        channels=channels,
    )


def replace_common(
    cursor: ChangesCursor,
    *,
    pts: int,
    qts: int,
    date: int,
    seq: int,
) -> ChangesCursor:
    return ChangesCursor(
        pts=pts, qts=qts, date=date, seq=seq, channels=dict(cursor.channels)
    )


def _nonneg_int(value, label: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise PolicyError(f"changes cursor has invalid {label}; run: tg changes --init")
    return value


def _marked_peer(key) -> int:
    if isinstance(key, int):
        peer = key
    elif isinstance(key, str) and key.lstrip("-").isdigit():
        peer = int(key)
    else:
        raise PolicyError("changes cursor is corrupt; run: tg changes --init")
    if peer >= 0:
        raise PolicyError(
            "changes cursor channel ids must be marked (-100…); run: tg changes --init"
        )
    return peer
