"""Typed job vocabulary and validation for ADR-0087."""

from __future__ import annotations

import json
from hashlib import sha256

from tgcli.archive import transcribe as transcribe_mod
from tgcli.errors import PolicyError

LANES = ("telegram", "local")
STATES = ("queued", "running", "completed", "failed", "cancelled")
PRIORITY_VALUES = {"low": 0, "normal": 1, "high": 2}
PRIORITY_NAMES = {value: name for name, value in PRIORITY_VALUES.items()}
MAX_RUNTIME_SECONDS = 3000.0
EVENT_LIMIT = 200


def validate_key(value: str) -> str:
    if not value or value != value.strip() or any(ord(char) < 32 for char in value):
        raise PolicyError(
            "job key must be a non-empty value without control characters"
        )
    return value


def validate_priority(value: str | None) -> str:
    priority = "normal" if value is None else value
    if priority not in PRIORITY_VALUES:
        raise PolicyError("job priority must be low, normal, or high")
    return priority


def transcribe_spec(max_attempts: int | None) -> dict[str, int]:
    return {"max_attempts": transcribe_mod.validate_max_attempts(max_attempts)}


def canonical_json(value: dict) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def spec_hash(kind: str, spec: dict, priority: str) -> str:
    payload = canonical_json({"kind": kind, "priority": priority, "spec": spec})
    return sha256(payload.encode("utf-8")).hexdigest()
