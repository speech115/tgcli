"""Opaque versioned cursor for `tg changes` (ADR-0063 / ADR-0103).

Pure functions only — no I/O. The caller owns the string between invocations.
"""

from __future__ import annotations

import base64
import hmac
import json
from dataclasses import dataclass, field

from tgcli.errors import PolicyError

CURSOR_PREFIX = "v1:"
BOUND_CURSOR_PREFIX = "v2:"
_BINDING_CONTEXT = b"tgcli changes cursor v2"
_BINDING_FIELD = "binding"
_BOUND_FIELDS = {"pts", "qts", "date", "seq", "channels", _BINDING_FIELD}
_TOKEN_CHARS = frozenset(
    "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-_"
)


@dataclass(frozen=True)
class ChangesCursor:
    pts: int
    qts: int
    date: int  # unix seconds
    seq: int
    channels: dict[int, int] = field(default_factory=dict)
    # channels: marked peer id (-100…) → pts


def account_binding_key(*, alias: str, api_id: int, api_hash: str) -> bytes:
    """Derive a stable per-account key without adding persistent cursor state."""
    identity = f"{alias}\0{api_id}".encode()
    return hmac.digest(api_hash.encode(), _BINDING_CONTEXT + b"\0" + identity, "sha256")


def _payload(cursor: ChangesCursor) -> dict:
    return {
        "pts": cursor.pts,
        "qts": cursor.qts,
        "date": cursor.date,
        "seq": cursor.seq,
        "channels": {str(peer): pts for peer, pts in sorted(cursor.channels.items())},
    }


def _raw(payload: dict) -> bytes:
    return json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()


def _token(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def _binding(payload: dict, binding_key: bytes) -> str:
    return _token(hmac.digest(binding_key, _raw(payload), "sha256"))


def encode(cursor: ChangesCursor, *, binding_key: bytes | None = None) -> str:
    payload = _payload(cursor)
    prefix = CURSOR_PREFIX
    if binding_key is not None:
        payload[_BINDING_FIELD] = _binding(payload, binding_key)
        prefix = BOUND_CURSOR_PREFIX
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode()
    return prefix + base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def decode(
    text: str,
    *,
    binding_key: bytes | None = None,
    require_bound: bool = False,
) -> ChangesCursor:
    if not isinstance(text, str) or not text:
        raise PolicyError("changes cursor is required; run: tg changes --init")
    if text.startswith(BOUND_CURSOR_PREFIX):
        prefix = BOUND_CURSOR_PREFIX
        is_bound = True
    elif text.startswith(CURSOR_PREFIX):
        prefix = CURSOR_PREFIX
        is_bound = False
    else:
        raise PolicyError(
            "changes cursor is missing or unsupported; run: tg changes --init"
        )
    padded = text[len(prefix) :]
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
    cursor = ChangesCursor(pts=pts, qts=qts, date=date, seq=seq, channels=channels)
    if is_bound:
        supplied = payload.get(_BINDING_FIELD)
        if (
            set(payload) != _BOUND_FIELDS
            or not isinstance(supplied, str)
            or len(supplied) != 43
            or any(char not in _TOKEN_CHARS for char in supplied)
        ):
            raise PolicyError("changes cursor is corrupt; run: tg changes --init")
        if binding_key is not None and not hmac.compare_digest(
            supplied, _binding(_payload(cursor), binding_key)
        ):
            raise _bound_error()
    if require_bound and (not is_bound or binding_key is None):
        raise _bound_error()
    return cursor


def _bound_error() -> PolicyError:
    return PolicyError(
        "changes cursor is not bound to this account or was modified; "
        "run: tg changes --init"
    )


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
