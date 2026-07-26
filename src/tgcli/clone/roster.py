"""Best-effort source-side participant snapshot for a clone (ADR-0024).

Runs at the end of `sync`, after message copying, and records who is in the
source channel and its linked discussion group where Telegram permits. Access
refusals are honest markers, never errors; a FloodWait defers a peer without
arming the main clone cooldown, so it can never block the next message sync.
"""

import json
from pathlib import Path

from telethon import errors as telethon_errors

from tgcli import atomic
from telethon.tl import types

from tgcli.clone import state

_ACCESS_ERRORS = (
    telethon_errors.ChatAdminRequiredError,
    telethon_errors.ChannelPrivateError,
    telethon_errors.ChatForbiddenError,
)


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
    try:
        async for participant in tg.iter_participants(entity):
            rows.append(_row(peer, participant))
    except telethon_errors.FloodWaitError:
        return [], "deferred", "flood_wait"
    except _ACCESS_ERRORS as exc:
        return [], "unavailable", type(exc).__name__
    except ValueError as exc:
        return [], "unavailable", str(exc)
    return rows, "collected", None


def _write(clone_id: str, rows: list[dict]) -> Path:
    directory = state.clones_dir()
    directory.mkdir(parents=True, exist_ok=True)
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

    if clone_state.comments == "enabled" and clone_state.discussion_source_peer_id:
        try:
            group = await tg.get_entity(
                types.PeerChannel(clone_state.discussion_source_peer_id)
            )
        except telethon_errors.FloodWaitError:
            discussion = _marker(clone_state, "deferred", "flood_wait")
        except _ACCESS_ERRORS as exc:
            # Telegram refuses to name a peer with an RPCError, not a
            # ValueError — the same shape that reached `attribution._resolve`
            # live. The roster runs after the copying is done; a refusal here
            # is a marker, never an exception out of a finished sync.
            discussion = _marker(clone_state, "unavailable", type(exc).__name__)
        except ValueError:
            discussion = _marker(
                clone_state, "unavailable", "source discussion group is unresolved"
            )
        else:
            group_rows, group_status, group_reason = await _collect_peer(
                tg, "discussion", group
            )
            rows.extend(group_rows)
            discussion = {
                "peer_id": clone_state.discussion_source_peer_id,
                "status": group_status,
                "count": len(group_rows),
                "reason": group_reason,
            }
    else:
        discussion = {"peer_id": None, "status": "none", "count": 0, "reason": None}

    path = _write(clone_state.clone_id, rows)
    return {"path": str(path), "source": source, "discussion": discussion}
