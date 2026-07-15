"""Copy broadcast channels into user-owned channels (ADR-0017)."""

from datetime import UTC, datetime, timedelta
from math import ceil
import secrets

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
            clone["destination_id"], clone["cursor"], clone["copied"],
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
            "source": {"id": clone_state.source_peer_id,
                       "title": clone_state.source_title},
            "destination": {"id": clone_state.destination_peer_id,
                            "title": getattr(destination, "title", clone_state.source_title)},
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
        raise RateLimitError(f"rate limited for {retry_after}s", retry_after=retry_after)


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
    return [(clone["status"], clone["id"], clone["source"]["id"],
             None if destination is None else destination["id"])]


def _confirmed_destination_ids(response, random_ids: list[int]) -> list[int]:
    updates = getattr(response, "updates", ())
    confirmations = [(update.random_id, update.id) for update in updates
                     if isinstance(update, types.UpdateMessageID)]
    matches = dict(confirmations)
    destination_ids = [matches.get(random_id) for random_id in random_ids]
    if (len(confirmations) != len(random_ids) or set(matches) != set(random_ids)
            or any(isinstance(item, bool) or not isinstance(item, int) or item <= 0
                   for item in destination_ids)
            or len(set(destination_ids)) != len(destination_ids)):
        raise PolicyError("Telegram did not confirm the complete cloned batch")
    return destination_ids


_NATIVE_MEDIA_TYPES = (
    types.MessageMediaWebPage, types.MessageMediaPhoto, types.MessageMediaDocument
)


def _unsupported_kind(source_entity, message) -> str | None:
    media = getattr(message, "media", None)
    if (getattr(source_entity, "noforwards", False)
            or getattr(message, "noforwards", False)
            or getattr(message, "reply_to", None) is not None):
        raise PolicyError("media/reply handling belongs to a later clone task")
    return None if media is None or isinstance(media, _NATIVE_MEDIA_TYPES) \
        else type(media).__name__


async def _forward_batch(tg, source, destination, clone_state, account_alias, messages):
    source_ids = [message.id for message in messages]
    random_ids = [secrets.randbelow(2**63 - 1) + 1 for _ in messages]
    safety.append_audit("clone-sync-forward", account_alias,
                        {"clone_id": clone_state.clone_id, "source_message_ids": source_ids})
    response = await _mutate(
        tg,
        functions.messages.ForwardMessagesRequest(
            from_peer=source, id=source_ids, random_id=random_ids,
            to_peer=destination, drop_author=True,
        ),
        clone_state,
    )
    destination_ids = _confirmed_destination_ids(response, random_ids)
    for source_id, destination_id in zip(source_ids, destination_ids, strict=True):
        clone_state.record_mapping(source_id, destination_id)
    clone_state.cursor = source_ids[-1]
    state.save(clone_state)
    return len(source_ids)


async def sync_text(tg, source: str, account_alias: str,
                    *, limit: int | None = None) -> dict:
    source_entity = await _resolve_source(tg, source)
    me = await tg.get_me()
    clone_state = state.load(state.clone_id(me.id, source_entity.id))
    if clone_state is None or clone_state.destination_peer_id is None:
        raise PolicyError("clone is not initialized; run clone init first")
    _enforce_cooldown(clone_state)
    try:
        destination = await tg.get_entity(
            types.PeerChannel(clone_state.destination_peer_id)
        )
    except ValueError:
        raise PolicyError("clone destination is unavailable") from None
    if not _is_private_owned_broadcast(destination):
        raise PolicyError("clone destination is not a private owned broadcast channel")

    latest = await tg.get_messages(destination, limit=1)
    destination_last_id = latest[0].id if latest else 0
    baseline = clone_state.max_destination_id() or 1
    if destination_last_id > baseline:
        raise PolicyError(
            "clone destination has unexpected tail messages; manual repair is required",
            unexpected=destination_last_id - baseline,
        )

    copied = 0
    copied_batches = 0
    skipped_service = 0
    skipped_unsupported = []
    more = False
    active_album = []
    async def finish_batch(messages) -> None:
        nonlocal copied, copied_batches
        unsupported = [
            {"id": message.id, "kind": kind}
            for message in messages
            if (kind := _unsupported_kind(source_entity, message)) is not None
        ]
        if unsupported:
            skipped_unsupported.extend(unsupported)
            clone_state.cursor = messages[-1].id
            state.save(clone_state)
            return
        copied += await _forward_batch(
            tg, source_entity, destination, clone_state, account_alias, messages
        )
        copied_batches += 1

    async for source_message in tg.iter_messages(
            source_entity, min_id=clone_state.cursor, reverse=True):
        if getattr(source_message, "action", None) is not None:
            if active_album:
                await finish_batch(active_album)
                active_album = []
            if limit is not None and copied_batches >= limit:
                more = True
                break
            skipped_service += 1
            clone_state.cursor = source_message.id
            state.save(clone_state)
            continue

        grouped_id = getattr(source_message, "grouped_id", None)
        if grouped_id is not None:
            if isinstance(grouped_id, bool) or not isinstance(grouped_id, int):
                raise PolicyError("clone album group id is invalid")
            if active_album and active_album[0].grouped_id == grouped_id:
                active_album.append(source_message)
                continue
            if active_album:
                await finish_batch(active_album)
                active_album = []
            if limit is not None and copied_batches >= limit:
                more = True
                break
            active_album = [source_message]
            continue
        if active_album:
            await finish_batch(active_album)
            active_album = []
        if limit is not None and copied_batches >= limit:
            more = True
            break
        await finish_batch([source_message])

    if active_album:
        await finish_batch(active_album)

    clone_state.last_synced_at = datetime.now(UTC).isoformat()
    state.save(clone_state)
    return {
        "clone": {
            "id": clone_state.clone_id,
            "source": {"id": source_entity.id, "title": clone_state.source_title},
            "destination": {"id": destination.id, "title": destination.title},
        },
        "sync": {"copied": copied, "skipped_service": skipped_service,
                 "skipped_unsupported": skipped_unsupported,
                 "cursor": clone_state.cursor, "more": more},
    }


def sync_rows(data: dict) -> list[tuple]:
    clone = data["clone"]
    sync = data["sync"]
    return [(sync["copied"], sync["skipped_service"],
             len(sync["skipped_unsupported"]), sync["cursor"], clone["id"],
             clone["source"]["id"], clone["destination"]["id"], sync["more"])]
