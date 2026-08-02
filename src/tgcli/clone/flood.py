"""Per-account clone flood cooldown record (ADR-0045).

One JSON file under the clones directory, keyed by account_user_id:
`{"cooldown_until": ISO|null, "last_peer_created_at": ISO|null}`.

Reads fail open (absent/corrupt → empty record). Writes go through
`tgcli.atomic.replace_text`.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from tgcli import atomic
from tgcli.clone import state

_EMPTY = {"cooldown_until": None, "last_peer_created_at": None}

# The deadline is wall-clock, so a host clock that ran ahead when the cooldown
# was armed (or was stepped back afterwards) persists a value no honest
# FloodWait can produce, and every clone command for that account exits 5
# forever with no way out but hand-editing the record. Telegram's longest
# realistic FloodWait on the mutations clone performs is a day, so a deadline
# further ahead than that is skew or corruption: clamp it when READING so it
# degrades to a bounded wait. Arming stays honest — the raw value is stored.
MAX_COOLDOWN_S = 86_400


def path_for(account_user_id: int) -> Path:
    return state.clones_dir() / f"account-{account_user_id}.json"


def _require_aware(value: datetime) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("datetime must be timezone-aware")
    return value.astimezone(UTC)


def _empty() -> dict:
    return dict(_EMPTY)


def load(account_user_id: int) -> dict:
    path = path_for(account_user_id)
    try:
        raw = path.read_text()
    except FileNotFoundError:
        return _empty()
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return _empty()
    if type(data) is not dict:
        return _empty()
    return {
        "cooldown_until": data.get("cooldown_until"),
        "last_peer_created_at": data.get("last_peer_created_at"),
    }


def _save(account_user_id: int, record: dict) -> None:
    directory = state.clones_dir()
    directory.mkdir(parents=True, exist_ok=True)
    atomic.replace_text(
        path_for(account_user_id), json.dumps(record, ensure_ascii=False)
    )


def arm_cooldown(account_user_id: int, deadline: datetime) -> None:
    record = load(account_user_id)
    aware = _require_aware(deadline)
    existing = cooldown_deadline(account_user_id)
    if existing is not None and existing > aware:
        aware = existing
    record["cooldown_until"] = aware.isoformat()
    _save(account_user_id, record)


def record_peer_created(account_user_id: int, at: datetime) -> None:
    record = load(account_user_id)
    record["last_peer_created_at"] = _require_aware(at).isoformat()
    _save(account_user_id, record)


def cooldown_deadline(account_user_id: int) -> datetime | None:
    until = load(account_user_id).get("cooldown_until")
    if until is None:
        return None
    try:
        deadline = datetime.fromisoformat(until)
    except (TypeError, ValueError):
        return None
    if deadline.tzinfo is None or deadline.utcoffset() is None:
        return None
    now = datetime.now(UTC)
    clamped = min(deadline.astimezone(UTC), now + timedelta(seconds=MAX_COOLDOWN_S))
    if clamped <= now:
        return None
    return clamped
