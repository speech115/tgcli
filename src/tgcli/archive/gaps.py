"""Archive gap records: their shape and how a stored list is read back."""

from __future__ import annotations

import json
from typing import Any

UNREADABLE_REASON = "unreadableGap"


def gap(scope, *, reason: str) -> dict[str, Any]:
    recover: dict[str, Any] = {"edits_deletes": "lost"}
    if scope == "common":
        recover["creation"] = (
            "re-read chats of interest with: tg read CHAT --after-id N"
        )
    else:
        recover["creation"] = f"re-read with: tg read {scope} --after-id N"
    return {"scope": scope, "reason": reason, "recover": recover}


def parse(raw: str | None) -> list[dict[str, Any]]:
    """Every stored gap, oldest first.

    Accepts the v10 list and the v9 single object. A value that is neither
    still means something was lost, so it reads as one ``unreadableGap``
    that only ``rebaseline`` clears, never as no gap.
    """
    if not raw:
        return []
    try:
        stored = json.loads(raw)
    except ValueError:
        stored = None
    if isinstance(stored, dict):
        return [stored]
    if isinstance(stored, list) and all(isinstance(g, dict) for g in stored):
        return stored
    return [gap("common", reason=UNREADABLE_REASON)]


def encode(gaps: list[dict[str, Any]]) -> str:
    return json.dumps(gaps, ensure_ascii=False, separators=(",", ":"))
