"""tg clone — copy a broadcast channel into a user-owned channel (ADR-0017).

Read window first: `list_clones` reports the state of every clone without
touching the network. Later tasks add init/sync on top of the same state files.
"""

from datetime import UTC, datetime, timedelta
from math import ceil

from telethon import errors as telethon_errors
from telethon.tl import functions, types

from tgcli import chatref, safety
from tgcli.clone import state
from tgcli.errors import NotFoundError, PolicyError, RateLimitError


def _entry(s: state.CloneState) -> dict:
    return {
        "clone_id": s.clone_id,
        "source": {"id": s.source_peer_id, "title": s.source_title},
        "destination_id": s.destination_peer_id,
        "cursor": s.cursor,
        "copied": len(s.id_map),
        "cooldown_until": s.retry_not_before,
        "created_at": s.created_at,
        "last_synced_at": s.last_synced_at,
    }


def _matches(s: state.CloneState, source: str | None) -> bool:
    if source is None:
        return True
    if source.lstrip("-").isdigit():
        return s.source_peer_id == int(source)
    return source.casefold() in s.source_title.casefold()


def list_clones(source: str | None = None) -> dict:
    directory = state.clones_dir()
    entries = []
    if directory.exists():
        for path in directory.glob("*.json"):
            loaded = state.load(path.stem)
            if loaded is not None and _matches(loaded, source):
                entries.append(_entry(loaded))
    entries.sort(key=lambda entry: entry["created_at"])
    return {"clones": entries}


def status_rows(data: dict) -> list[tuple]:
    return [
        (
            clone["source"]["id"],
            clone["source"]["title"],
            clone["destination_id"],
            clone["cursor"],
            clone["copied"],
            clone["last_synced_at"],
        )
        for clone in data["clones"]
    ]


async def _resolve_source(tg, source: str):
    try:
        entity = await tg.get_entity(chatref.parse(source))
    except ValueError:
        raise NotFoundError(f"channel not found: {source!r}") from None
    if not getattr(entity, "broadcast", False) or getattr(entity, "megagroup", False):
        raise PolicyError("clone source must be a broadcast channel")
    return entity


async def preview_init(tg, source: str) -> dict:
    entity = await _resolve_source(tg, source)
    me = await tg.get_me()
    total = (await tg.get_messages(entity, limit=0)).total
    clone_id = state.clone_id(me.id, entity.id)
    preview = safety.create_preview(
        {
            "kind": "clone-init",
            "source": source,
            "account_user_id": me.id,
            "source_peer_id": entity.id,
            "source_title": entity.title,
            "protected": bool(getattr(entity, "noforwards", False)),
            "approximate_message_count": total,
        }
    )
    return {
        "preview_id": preview["preview_id"],
        "expires_at": preview["expires_at"],
        "clone": {
            "id": clone_id,
            "source": {"id": entity.id, "title": entity.title},
            "destination": None,
            "status": "planned",
            "commit_required": True,
        },
        "approximate_message_count": total,
        "protected": preview["protected"],
    }


def _is_private_owned_broadcast(entity, *, title: str | None = None) -> bool:
    active_usernames = any(
        getattr(item, "active", False)
        for item in (getattr(entity, "usernames", None) or ())
    )
    return bool(
        (title is None or getattr(entity, "title", None) == title)
        and getattr(entity, "creator", False)
        and getattr(entity, "broadcast", False)
        and not getattr(entity, "megagroup", False)
        and getattr(entity, "username", None) is None
        and not active_usernames
    )


async def _marker_candidates(tg, marker: str) -> tuple[list[object], list[object]]:
    valid = []
    wrong_shape = []
    async for dialog in tg.iter_dialogs():
        entity = getattr(dialog, "entity", None)
        if getattr(entity, "title", None) != marker:
            continue
        (valid if _is_private_owned_broadcast(entity, title=marker) else wrong_shape).append(entity)
    return valid, wrong_shape


def _init_result(clone_state: state.CloneState, destination) -> dict:
    return {
        "clone": {
            "id": clone_state.clone_id,
            "source": {
                "id": clone_state.source_peer_id,
                "title": clone_state.source_title,
            },
            "destination": {
                "id": clone_state.destination_peer_id,
                "title": getattr(destination, "title", clone_state.source_title),
            },
            "status": "ready",
            "commit_required": False,
        }
    }


def _enforce_cooldown(clone_state: state.CloneState) -> None:
    deadline = clone_state.cooldown_deadline()
    if deadline is None:
        return
    retry_after = ceil((deadline - datetime.now(UTC)).total_seconds())
    if retry_after > 0:
        raise RateLimitError(
            f"rate limited for {retry_after}s", retry_after=retry_after
        )


async def _mutate(tg, request, clone_state: state.CloneState):
    try:
        return await tg(request)
    except telethon_errors.FloodWaitError as exc:
        clone_state.set_cooldown(datetime.now(UTC) + timedelta(seconds=exc.seconds))
        state.save(clone_state)
        raise


async def commit_init(tg, source: str, account_alias: str, payload: dict) -> dict:
    entity = await _resolve_source(tg, source)
    me = await tg.get_me()
    if me.id != payload["account_user_id"] or entity.id != payload["source_peer_id"]:
        raise PolicyError("clone init preview no longer matches the source or account")
    clone_id = state.clone_id(me.id, entity.id)
    clone_state = state.load(clone_id) or state.CloneState.new(
        account_user_id=me.id,
        source_peer_id=entity.id,
        source_title=payload["source_title"],
    )
    marker = clone_state.creation_marker or f"tgcli-clone-{clone_id[:12]}"
    clone_state.creation_marker = marker
    state.save(clone_state)
    _enforce_cooldown(clone_state)

    if clone_state.destination_peer_id is not None:
        try:
            destination = await tg.get_entity(
                types.PeerChannel(clone_state.destination_peer_id)
            )
        except ValueError:
            raise PolicyError("clone destination is unavailable") from None
        if not _is_private_owned_broadcast(destination):
            raise PolicyError(
                "clone destination is not a private owned broadcast channel"
            )
    else:
        valid, wrong_shape = await _marker_candidates(tg, marker)
        if len(valid) + len(wrong_shape) > 1:
            raise PolicyError("clone destination marker matched multiple channels")
        if wrong_shape:
            raise PolicyError(
                "clone destination marker matched a channel with wrong shape"
            )
        if valid:
            destination = valid[0]
        else:
            safety.append_audit(
                "clone-init-create", account_alias, {"clone_id": clone_id}
            )
            update = await _mutate(
                tg,
                functions.channels.CreateChannelRequest(
                    title=marker, about="", broadcast=True, megagroup=False
                ),
                clone_state,
            )
            candidates = [
                item
                for item in getattr(update, "chats", ())
                if _is_private_owned_broadcast(item, title=marker)
            ]
            if len(candidates) != 1:
                raise PolicyError(
                    "Telegram did not return the created private channel"
                )
            destination = candidates[0]
        clone_state.destination_peer_id = destination.id
        state.save(clone_state)

    if getattr(destination, "title", None) != clone_state.source_title:
        safety.append_audit(
            "clone-init-title", account_alias, {"clone_id": clone_id}
        )
        await _mutate(
            tg,
            functions.channels.EditTitleRequest(
                channel=destination, title=clone_state.source_title
            ),
            clone_state,
        )
        destination.title = clone_state.source_title
    return _init_result(clone_state, destination)


def init_rows(data: dict) -> list[tuple]:
    clone = data["clone"]
    destination = clone["destination"]
    return [
        (
            clone["status"],
            clone["id"],
            clone["source"]["id"],
            None if destination is None else destination["id"],
        )
    ]
