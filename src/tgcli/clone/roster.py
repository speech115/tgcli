"""Best-effort source-side participant snapshot for a clone (ADR-0024).

Runs at the end of `sync`, after message copying, and records who is in the
source channel and its linked discussion group where Telegram permits. Access
refusals are honest markers, never errors; a FloodWait defers a peer, and the
cooldown it arms covers participant listing only, so it never blocks the next
message sync.
"""

import json
from pathlib import Path

from telethon import errors as telethon_errors
from telethon.tl import types

from tgcli import atomic
from tgcli.clone import state
from tgcli.clone.state import Comments
from tgcli.governor import pacing
from tgcli.tg_errors import FLOOD_ERRORS, TRANSIENT_ERRORS, unless_refused


async def _discussion_roster(tg, clone_state, group, refusal, rows: list) -> dict:
    if refusal is not None:
        # The roster runs after the copying is done; a refusal here is a
        # marker, never an exception out of a finished sync.
        reason = (
            type(refusal).__name__
            if isinstance(refusal, telethon_errors.RPCError)
            else "source discussion group is unresolved"
        )
        return _marker(clone_state, "unavailable", reason)
    group_rows, group_status, group_reason = await _collect_peer(
        tg, "discussion", group
    )
    rows.extend(group_rows)
    return {
        "peer_id": clone_state.discussion_source_peer_id,
        "status": group_status,
        "count": len(group_rows),
        "reason": group_reason,
    }


def _deferred_reason(exc: Exception) -> str:
    if isinstance(exc, FLOOD_ERRORS):
        return "flood_wait"
    return type(exc).__name__


def path_for(clone_id: str) -> Path:
    return state.clones_dir() / f"{clone_id}-participants.jsonl"


def _row(peer: str, participant) -> dict:
    return {
        "peer": peer,
        "id": participant.id,
        "username": getattr(participant, "username", None),
        "first_name": getattr(participant, "first_name", None),
        "last_name": getattr(participant, "last_name", None),
        "phone": getattr(participant, "phone", None),
        "is_bot": bool(getattr(participant, "bot", False)),
    }


async def _collect_peer(tg, peer: str, entity) -> tuple[list[dict], str, str | None]:
    rows = []
    if pacing.wall_clock_exhausted():
        return [], "deferred", "wall_clock_cap"
    try:
        async for participant in tg.iter_participants(entity):
            if pacing.wall_clock_exhausted():
                # --max-runtime bounds the whole sync, the listing included.
                return [], "deferred", "wall_clock_cap"
            rows.append(_row(peer, participant))
    except TRANSIENT_ERRORS as exc:
        return [], "deferred", _deferred_reason(exc)
    except telethon_errors.UnauthorizedError:
        raise
    except telethon_errors.RPCError as exc:
        return [], "unavailable", type(exc).__name__
    except ValueError as exc:
        return [], "unavailable", str(exc)
    return rows, "collected", None


def _write(clone_id: str, rows: list[dict]) -> Path:
    state.ensure_clones_dir()
    path = path_for(clone_id)
    atomic.replace_text(
        path, "".join(json.dumps(row, ensure_ascii=False) + "\n" for row in rows)
    )
    return path


def _marker(clone_state: state.CloneState, status: str, reason: str) -> dict:
    return {
        "peer_id": clone_state.discussion_source_peer_id,
        "status": status,
        "count": 0,
        "reason": reason,
    }


def deferred(clone_state: state.CloneState, reason: str) -> dict:
    """The roster this run skipped: every peer deferred, sidecar untouched."""
    source = {
        "peer_id": clone_state.source_peer_id,
        "status": "deferred",
        "count": 0,
        "reason": reason,
    }
    if (
        clone_state.comments == Comments.ENABLED
        and clone_state.discussion_source_peer_id
    ):
        discussion = _marker(clone_state, "deferred", reason)
    else:
        discussion = {"peer_id": None, "status": "none", "count": 0, "reason": None}
    path = path_for(clone_state.clone_id)
    return {"path": str(path), "source": source, "discussion": discussion}


async def collect(tg, clone_state: state.CloneState, source_entity) -> dict:
    """Snapshot the source channel and its discussion group; return a status
    dict and rewrite the participant sidecar atomically."""
    rows: list[dict] = []
    source_rows, source_status, source_reason = await _collect_peer(
        tg, "source", source_entity
    )
    rows.extend(source_rows)
    source = {
        "peer_id": clone_state.source_peer_id,
        "status": source_status,
        "count": len(source_rows),
        "reason": source_reason,
    }

    if not (
        clone_state.comments == Comments.ENABLED
        and clone_state.discussion_source_peer_id
    ):
        discussion = {"peer_id": None, "status": "none", "count": 0, "reason": None}
    elif pacing.wall_clock_exhausted():
        discussion = _marker(clone_state, "deferred", "wall_clock_cap")
    else:
        try:
            group, refusal = await unless_refused(
                tg.get_entity(types.PeerChannel(clone_state.discussion_source_peer_id))
            )
        except TRANSIENT_ERRORS as exc:
            discussion = _marker(clone_state, "deferred", _deferred_reason(exc))
        else:
            discussion = await _discussion_roster(tg, clone_state, group, refusal, rows)

    path = _write(clone_state.clone_id, rows)
    return {"path": str(path), "source": source, "discussion": discussion}
