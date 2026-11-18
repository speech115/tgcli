"""Archive gap records: what a lost stretch of updates looks like on disk.

A gap is written when the common update cursor could not be followed
(``differenceTooLong``, or a stored cursor that no longer decodes). Archive
sync then reads every chat that moved by ``min_id`` and clears the gaps once
no catch-up is left; edits and deletions inside the lost stretch stay lost.
"""

from __future__ import annotations

import json
from typing import Any

UNREADABLE_REASON = "unreadableGap"


def gap(scope, *, reason: str) -> dict[str, Any]:
    return {
        "scope": scope,
        "reason": reason,
        "recover": {
            "edits_deletes": "lost",
            "creation": "archive sync re-reads the chats that moved",
        },
    }


def parse(raw: str | None) -> list[dict[str, Any]]:
    """Every stored gap, oldest first.

    Accepts the v10 list and the v9 single object. A value that is neither
    still means something was lost, so it reads as one ``unreadableGap``,
    never as no gap.
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
