"""Copy supported Telegram chats into user-owned channels (ADR-0017/0021)."""
from datetime import UTC, datetime, timedelta
from math import ceil
from pathlib import Path
import secrets, tempfile

from telethon import errors as telethon_errors, utils as telethon_utils
from telethon.tl import functions, types

from tgcli import chatref, safety
from tgcli.clone import attribution, fidelity, profile, replies, state
from tgcli.errors import NotFoundError, PolicyError, RateLimitError

def _entry(s: state.CloneState) -> dict:
    return {"clone_id": s.clone_id, "source": {"id": s.source_peer_id,
            "title": s.source_title, "kind": s.source_kind},
            "destination_id": s.destination_peer_id,
            "cursor": s.cursor, "copied": len(s.id_map), "cooldown_until": s.retry_not_before,
            "created_at": s.created_at, "last_synced_at": s.last_synced_at}

def _matches(s: state.CloneState, source: str | None) -> bool:
    return source is None or (s.source_peer_id == int(source)
        if source.lstrip("-").isdigit() else source.casefold() in s.source_title.casefold())

def list_clones(source: str | None = None) -> dict:
    directory = state.clones_dir()
    entries = [_entry(loaded) for path in directory.glob("*.json")
               if (loaded := state.load(path.stem)) is not None
               and _matches(loaded, source)] if directory.exists() else []
    entries.sort(key=lambda entry: entry["created_at"])
    return {"clones": entries}

def status_rows(data: dict) -> list[tuple]:
    return [(c["source"]["id"], c["source"]["title"], c["source"]["kind"],
             c["destination_id"], c["cursor"],
             c["copied"], c["last_synced_at"]) for c in data["clones"]]

async def _resolve_source(tg, source: str):
    try:
        entity = await tg.get_entity(chatref.parse(source))
    except ValueError:
        raise NotFoundError(f"clone source not found: {source!r}") from None
    kind = attribution.source_kind(entity)
    return entity, kind, attribution.display_name(entity)

async def preview_init(tg, source: str) -> dict:
    entity, source_kind, source_title = await _resolve_source(tg, source)
    me = await tg.get_me()
    total = (await tg.get_messages(entity, limit=0)).total
    clone_id = state.clone_id(me.id, entity.id)
    preview = safety.create_preview({"kind": "clone-init", "source": source,
        "account_user_id": me.id, "source_peer_id": entity.id,
        "source_title": source_title, "source_kind": source_kind,
        "protected": bool(getattr(entity, "noforwards", False)),
        "approximate_message_count": total})
    return {"preview_id": preview["preview_id"], "expires_at": preview["expires_at"],
            "clone": {"id": clone_id, "source": {"id": entity.id,
            "title": source_title, "kind": source_kind},
            "destination": None, "status": "planned", "commit_required": True},
            "approximate_message_count": total,
            "protected": preview["protected"]}

def _is_private_owned_broadcast(entity, *, title: str | None = None) -> bool:
    active = any(getattr(item, "active", False)
                 for item in (getattr(entity, "usernames", None) or ()))
    return bool((title is None or getattr(entity, "title", None) == title)
                and getattr(entity, "creator", False)
                and getattr(entity, "broadcast", False)
                and not getattr(entity, "megagroup", False)
                and getattr(entity, "username", None) is None and not active)

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
    return {"clone": {"id": clone_state.clone_id, "source": {
            "id": clone_state.source_peer_id, "title": clone_state.source_title,
            "kind": clone_state.source_kind},
            "destination": {"id": clone_state.destination_peer_id,
            "title": getattr(destination, "title", clone_state.source_title)},
            "status": "ready", "commit_required": False}}

def _enforce_cooldown(clone_state: state.CloneState) -> None:
    deadline = clone_state.cooldown_deadline()
    if deadline is not None:
        retry_after = ceil((deadline - datetime.now(UTC)).total_seconds())
        if retry_after > 0:
            raise RateLimitError(f"rate limited for {retry_after}s", retry_after=retry_after)

async def _with_cooldown(awaitable, clone_state):
    try:
        return await awaitable
    except telethon_errors.FloodWaitError as exc:
        clone_state.set_cooldown(datetime.now(UTC) + timedelta(seconds=exc.seconds))
        state.save(clone_state)
        raise

async def _mutate(tg, request, clone_state: state.CloneState):
    return await _with_cooldown(tg(request), clone_state)

async def commit_init(tg, source: str, account_alias: str, payload: dict) -> dict:
    entity, source_kind, _ = await _resolve_source(tg, source)
    me = await tg.get_me()
    if (me.id != payload["account_user_id"] or entity.id != payload["source_peer_id"]
            or payload.get("source_kind", source_kind) != source_kind):
        raise PolicyError("clone init preview no longer matches the source or account")
    clone_id = state.clone_id(me.id, entity.id)
    clone_state = state.load(clone_id) or state.CloneState.new(
        account_user_id=me.id, source_peer_id=entity.id,
        source_title=payload["source_title"],
        source_kind=payload.get("source_kind", source_kind))
    marker = clone_state.creation_marker or f"tgcli-clone-{clone_id[:12]}"
    clone_state.creation_marker = marker
    state.save(clone_state)
    _enforce_cooldown(clone_state)
    if clone_state.destination_peer_id is not None:
        try:
            destination = await tg.get_entity(types.PeerChannel(
                clone_state.destination_peer_id))
        except ValueError:
            raise PolicyError("clone destination is unavailable") from None
        if not _is_private_owned_broadcast(destination):
            raise PolicyError("clone destination is not a private owned broadcast channel")
    else:
        valid, wrong_shape = await _marker_candidates(tg, marker)
        if len(valid) + len(wrong_shape) > 1:
            raise PolicyError("clone destination marker matched multiple channels")
        if wrong_shape:
            raise PolicyError("clone destination marker matched a channel with wrong shape")
        if valid:
            destination = valid[0]
        else:
            safety.append_audit("clone-init-create", account_alias,
                                {"clone_id": clone_id})
            update = await _mutate(
                tg, functions.channels.CreateChannelRequest(
                    title=marker, about="", broadcast=True, megagroup=False),
                clone_state)
            candidates = [item for item in getattr(update, "chats", ())
                          if _is_private_owned_broadcast(item, title=marker)]
            if len(candidates) != 1:
                raise PolicyError("Telegram did not return the created private channel")
            destination = candidates[0]
        clone_state.destination_peer_id = destination.id
        state.save(clone_state)
    if getattr(destination, "title", None) != clone_state.source_title:
        safety.append_audit("clone-init-title", account_alias, {"clone_id": clone_id})
        await _mutate(
            tg, functions.channels.EditTitleRequest(
                channel=destination, title=clone_state.source_title), clone_state)
        destination.title = clone_state.source_title
    await profile.copy(tg, entity, destination, account_alias, clone_id,
                       lambda awaitable: _with_cooldown(awaitable, clone_state))
    return _init_result(clone_state, destination)
def init_rows(data: dict) -> list[tuple]:
    clone = data["clone"]
    destination = clone["destination"]
    return [(clone["status"], clone["id"], clone["source"]["id"], None if destination is None else destination["id"])]
def _confirmed_destination_ids(response, random_ids: list[int]) -> list[int]:
    if isinstance(response, types.UpdateShortSentMessage):
        destination_id = response.id
        if (len(random_ids) == 1 and isinstance(destination_id, int)
                and not isinstance(destination_id, bool) and destination_id > 0):
            return [destination_id]
        raise PolicyError("Telegram did not confirm the complete cloned batch")
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
async def _uploaded_media(tg, message, path, clone_state):
    input_file = await _with_cooldown(tg.upload_file(str(path)), clone_state)
    if isinstance(message.media, types.MessageMediaPhoto):
        return types.InputMediaUploadedPhoto(file=input_file)
    document = message.media.document
    return types.InputMediaUploadedDocument(file=input_file,
        mime_type=getattr(document, "mime_type", None) or "application/octet-stream",
        attributes=list(getattr(document, "attributes", None) or ()))
async def _reupload_batch(tg, destination, clone_state, account_alias, messages,
                          random_ids, reply_to, author=None):
    with tempfile.TemporaryDirectory(prefix="tgcli-clone-reupload-") as workdir:
        downloads = {}
        for message in messages:
            media = getattr(message, "media", None)
            if media is None or isinstance(media, types.MessageMediaWebPage):
                continue
            downloaded = await _with_cooldown(
                tg.download_media(message, file=Path(workdir) / f"src-{message.id}"), clone_state)
            if downloaded is None:
                raise PolicyError(f"clone media download failed at source message {message.id}")
            downloads[message.id] = Path(downloaded)
        safety.append_audit("clone-sync-reupload", account_alias, {
            "clone_id": clone_state.clone_id, "source_message_ids": [m.id for m in messages]})
        if len(messages) == 1:
            message = messages[0]
            media = getattr(message, "media", None)
            text, entities = attribution.prefixed(
                message.message or "", message.entities, author)
            common = {"peer": destination, "message": text, "random_id": random_ids[0],
                      "reply_to": reply_to, "entities": entities}
            if media is None or isinstance(media, types.MessageMediaWebPage):
                request = functions.messages.SendMessageRequest(
                    **common, no_webpage=media is None)
            else:
                request = functions.messages.SendMediaRequest(**common, media=await _uploaded_media(
                    tg, message, downloads[message.id], clone_state))
            return await _mutate(tg, request, clone_state)
        multi_media = []
        for index, (message, random_id) in enumerate(
                zip(messages, random_ids, strict=True)):
            if message.id not in downloads:
                raise PolicyError(f"clone album item is not reconstructable: {message.id}")
            uploaded = await _uploaded_media(tg, message, downloads[message.id], clone_state)
            stored = await _mutate(tg, functions.messages.UploadMediaRequest(
                peer=destination, media=uploaded), clone_state)
            text, entities = attribution.prefixed(
                message.message or "", message.entities, author if index == 0 else None)
            multi_media.append(types.InputSingleMedia(media=telethon_utils.get_input_media(stored),
                random_id=random_id, message=text, entities=entities))
        request = functions.messages.SendMultiMediaRequest(
            peer=destination, multi_media=multi_media, reply_to=reply_to)
        return await _mutate(tg, request, clone_state)

async def _forward_batch(tg, source, destination, clone_state, account_alias,
                         messages, me, author_cache):
    source_ids = [message.id for message in messages]
    random_ids = [secrets.randbelow(2**63 - 1) + 1 for _ in messages]
    reply_to = replies.target(messages, clone_state, source)
    reply_flattened = getattr(messages[0], "reply_to", None) is not None and reply_to is None
    replacement = await fidelity.replacement(tg, messages[0]) if len(messages) == 1 else None
    reupload = (getattr(source, "noforwards", False) or reply_to is not None or
                any(getattr(message, "noforwards", False) for message in messages))
    author = None
    if clone_state.source_kind != "broadcast" and (replacement is not None or reupload):
        author = await attribution.author_name(
            tg, source, messages[0], me, author_cache,
            lambda awaitable: _with_cooldown(awaitable, clone_state))
    if replacement is not None:
        mode = "snapshots"
        text, entities = attribution.prefixed(replacement[0], replacement[1], author)
        safety.append_audit("clone-sync-snapshot", account_alias,
                            {"clone_id": clone_state.clone_id,
                             "source_message_ids": source_ids})
        response = await _mutate(tg, functions.messages.SendMessageRequest(
            peer=destination, message=text, random_id=random_ids[0],
            reply_to=reply_to, no_webpage=True, entities=entities), clone_state)
    elif not reupload:
        mode = "forwarded"
        safety.append_audit("clone-sync-forward", account_alias,
                            {"clone_id": clone_state.clone_id,
                             "source_message_ids": source_ids})
        request = functions.messages.ForwardMessagesRequest(
            from_peer=source, id=source_ids, random_id=random_ids,
            to_peer=destination, drop_author=clone_state.source_kind == "broadcast",
        )
        response = await _mutate(tg, request, clone_state)
    else:
        mode = "reuploaded"
        response = await _reupload_batch(tg, destination, clone_state, account_alias,
                                         messages, random_ids, reply_to, author)
    destination_ids = _confirmed_destination_ids(response, random_ids)
    for source_id, destination_id in zip(source_ids, destination_ids, strict=True):
        clone_state.record_mapping(source_id, destination_id)
    clone_state.cursor = source_ids[-1]
    state.save(clone_state)
    return len(source_ids), mode, reply_flattened

async def sync_text(tg, source: str, account_alias: str,
                    *, limit: int | None = None) -> dict:
    source_entity, source_kind, _ = await _resolve_source(tg, source)
    me = await tg.get_me()
    clone_state = state.load(state.clone_id(me.id, source_entity.id))
    if clone_state is None or clone_state.destination_peer_id is None:
        raise PolicyError("clone is not initialized; run clone init first")
    if clone_state.source_kind != source_kind:
        raise PolicyError("clone source kind no longer matches initialized state")
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
        tail = await tg.get_messages(destination, limit=destination_last_id - baseline)
        unexpected = [item for item in tail if item.id > baseline and getattr(item, "action", None) is None]
        if unexpected:
            raise PolicyError("clone destination has unexpected tail messages; manual repair is required",
                              unexpected=len(unexpected))
    copied = 0
    copied_batches = 0
    skipped_service = 0
    skipped_unsupported = []
    transport_counts = {"forwarded": 0, "reuploaded": 0, "snapshots": 0}
    reply_flattened = 0
    author_cache = {}
    more = False
    active_album = []
    async def finish_batch(messages) -> None:
        nonlocal copied, copied_batches, reply_flattened
        unsupported = [
            {"id": message.id, "kind": kind}
            for message in messages
            if (kind := fidelity.unsupported_kind(message)) is not None
        ]
        if unsupported:
            skipped_unsupported.extend(unsupported)
            clone_state.cursor = messages[-1].id
            state.save(clone_state)
            return
        batch_copied, mode, flattened = await _forward_batch(
            tg, source_entity, destination, clone_state, account_alias, messages,
            me, author_cache
        )
        copied += batch_copied
        transport_counts[mode] += batch_copied
        reply_flattened += int(flattened)
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
    return {"clone": {"id": clone_state.clone_id,
        "source": {"id": source_entity.id, "title": clone_state.source_title,
                   "kind": clone_state.source_kind},
        "destination": {"id": destination.id, "title": destination.title}},
        "sync": {"copied": copied, "skipped_service": skipped_service,
                 "skipped_unsupported": skipped_unsupported,
                 **transport_counts, "reply_flattened": reply_flattened,
                 "cursor": clone_state.cursor, "more": more}}

def sync_rows(data: dict) -> list[tuple]:
    clone = data["clone"]
    sync = data["sync"]
    return [(sync["copied"], sync["forwarded"], sync["reuploaded"], sync["snapshots"],
             sync["reply_flattened"], sync["skipped_service"], len(sync["skipped_unsupported"]),
             sync["cursor"], clone["id"], clone["source"]["id"], clone["destination"]["id"], sync["more"])]
