"""Copy supported Telegram chats into user-owned channels (ADR-0017/0021)."""

from datetime import UTC, datetime, timedelta
from math import ceil
from pathlib import Path
import secrets
from typing import Any
import tempfile
from telethon import errors as telethon_errors, utils as telethon_utils
from telethon.tl import functions, types
from tgcli import chatref, safety
from tgcli.clone import (
    attribution,
    batching,
    comments,
    discussion,
    fidelity,
    legs,
    quote_fallback,
    quotes,
    roster,
    snapshot,
    state,
    topics,
    transport,
)
from tgcli.errors import NotFoundError, PartialFailure, PolicyError, RateLimitError


def _entry(s: state.CloneState) -> dict:
    return {
        "clone_id": s.clone_id,
        "source": {
            "id": s.source_peer_id,
            "title": s.source_title,
            "kind": s.source_kind,
        },
        "destination_id": s.destination_peer_id,
        "cursor": s.cursor,
        "copied": len(s.id_map),
        "cooldown_until": s.retry_not_before,
        "created_at": s.created_at,
        "last_synced_at": s.last_synced_at,
        "comments": s.comments,
    }


def _matches(s: state.CloneState, source: str | None) -> bool:
    return source is None or (
        s.source_peer_id == int(source)
        if source.lstrip("-").isdigit()
        else source.casefold() in s.source_title.casefold()
    )


def _unreadable_entry(clone_id: str) -> dict:
    return {
        "clone_id": clone_id,
        "source": {"id": None, "title": None, "kind": None},
        "destination_id": None,
        "cursor": None,
        "copied": None,
        "cooldown_until": None,
        "created_at": "",
        "last_synced_at": None,
        "comments": None,
        "unreadable": True,
    }


def _load_entry(path, source: str | None) -> dict | None:
    """One clone-state file as a status entry, or None if it should be skipped.
    An unreadable file (corrupt/legacy) becomes a marked entry instead of
    crashing the whole listing; it is dropped from filtered listings because its
    identity cannot be matched against SOURCE."""
    try:
        loaded = state.load(path.stem)
    except PolicyError:
        return None if source is not None else _unreadable_entry(path.stem)
    if loaded is None or not _matches(loaded, source):
        return None
    return _entry(loaded)


def list_clones(source: str | None = None) -> dict:
    directory = state.clones_dir()
    entries = (
        [
            entry
            for path in directory.glob("*.json")
            if (entry := _load_entry(path, source)) is not None
        ]
        if directory.exists()
        else []
    )
    entries.sort(key=lambda entry: entry["created_at"])
    return {"clones": entries}


def status_rows(data: dict) -> list[tuple]:
    return [
        (
            c["source"]["id"],
            c["clone_id"] if c.get("unreadable") else c["source"]["title"],
            c["source"]["kind"],
            c["destination_id"],
            c["cursor"],
            c["copied"],
            c["last_synced_at"],
            "unreadable" if c.get("unreadable") else c["comments"],
        )
        for c in data["clones"]
    ]


async def _resolve_source(tg, source: str):
    try:
        entity = await tg.get_entity(chatref.parse(source))
    except ValueError:
        raise NotFoundError(f"clone source not found: {source!r}") from None
    kind = attribution.source_kind(entity)
    return entity, kind, attribution.display_name(entity)


def _supersede_status(clone_id: str, replace: bool) -> dict:
    """Read-only view of the state slot for the preview, without fail-closing on
    an unreadable (legacy/corrupt) file the way commit does."""
    try:
        existing = state.load(clone_id) is not None
        readable = True if existing else None
    except PolicyError:
        existing, readable = True, False
    return {"existing": existing, "readable": readable, "replace": replace}


async def preview_init(tg, source: str, *, replace: bool = False) -> dict:
    entity, source_kind, source_title = await _resolve_source(tg, source)
    me = await tg.get_me()
    total = (await tg.get_messages(entity, limit=0)).total
    clone_id = state.clone_id(me.id, entity.id)
    preview = safety.create_preview(
        {
            "kind": "clone-init",
            "source": source,
            "account_user_id": me.id,
            "source_peer_id": entity.id,
            "source_title": source_title,
            "source_kind": source_kind,
            "replace": replace,
            "protected": bool(getattr(entity, "noforwards", False)),
            "approximate_message_count": total,
        }
    )
    return {
        "preview_id": preview["preview_id"],
        "expires_at": preview["expires_at"],
        "clone": {
            "id": clone_id,
            "source": {"id": entity.id, "title": source_title, "kind": source_kind},
            "destination": None,
            "status": "planned",
            "commit_required": True,
        },
        "approximate_message_count": total,
        "protected": preview["protected"],
        "supersede": _supersede_status(clone_id, replace),
    }


def _is_private_owned_broadcast(entity, *, title: str | None = None) -> bool:
    active = any(
        getattr(item, "active", False)
        for item in (getattr(entity, "usernames", None) or ())
    )
    return bool(
        (title is None or getattr(entity, "title", None) == title)
        and getattr(entity, "creator", False)
        and getattr(entity, "broadcast", False)
        and not getattr(entity, "megagroup", False)
        and getattr(entity, "username", None) is None
        and not active
    )


async def _marker_candidates(tg, marker: str, shape_ok) -> tuple[list[Any], list[Any]]:
    valid = []
    wrong_shape = []
    async for dialog in tg.iter_dialogs():
        entity = getattr(dialog, "entity", None)
        if getattr(entity, "title", None) != marker:
            continue
        (valid if shape_ok(entity, title=marker) else wrong_shape).append(entity)
    return valid, wrong_shape


def _enforce_cooldown(clone_state: state.CloneState) -> None:
    deadline = clone_state.cooldown_deadline()
    if deadline is not None:
        retry_after = ceil((deadline - datetime.now(UTC)).total_seconds())
        if retry_after > 0:
            raise RateLimitError(
                f"rate limited for {retry_after}s", retry_after=retry_after
            )


async def _with_cooldown(awaitable, clone_state):
    try:
        return await awaitable
    except telethon_errors.FloodWaitError as exc:
        clone_state.set_cooldown(datetime.now(UTC) + timedelta(seconds=exc.seconds))
        state.save(clone_state)
        raise


async def _mutate(tg, request, clone_state: state.CloneState):
    return await _with_cooldown(tg(request), clone_state)


async def _copy_profile(tg, source, destination, account_alias, clone_id, cooldown):
    """Copies about/avatar onto destination; returns the source's full chat."""
    if isinstance(source, types.User):
        full = await cooldown(tg(functions.users.GetFullUserRequest(source)))  # type: ignore  # Telethon resolves the entity
        about = getattr(full.full_user, "about", None) or ""
    elif isinstance(source, types.Chat):
        full = await cooldown(
            tg(functions.messages.GetFullChatRequest(chat_id=source.id))
        )
        about = getattr(full.full_chat, "about", None) or ""
    else:
        full = await cooldown(tg(functions.channels.GetFullChannelRequest(source)))
        about = getattr(full.full_chat, "about", None) or ""
    if about:
        safety.append_audit(
            "clone-init-about",
            account_alias,
            {
                "clone_id": clone_id,
                "source_peer_id": source.id,
            },
        )
        try:
            await cooldown(
                tg(
                    functions.messages.EditChatAboutRequest(
                        peer=destination, about=about
                    )
                )
            )
        except telethon_errors.ChatAboutNotModifiedError:
            pass
    full_chat = getattr(full, "full_chat", None)
    photo = getattr(source, "photo", None)
    if photo is None or isinstance(
        photo, (types.ChatPhotoEmpty, types.UserProfilePhotoEmpty)
    ):
        return full_chat
    with tempfile.TemporaryDirectory(prefix="tgcli-clone-avatar-") as workdir:
        downloaded = await cooldown(
            tg.download_profile_photo(source, file=Path(workdir) / "avatar")
        )
        if downloaded is None:
            raise PolicyError("clone source avatar download failed")
        uploaded = await cooldown(tg.upload_file(downloaded))
        safety.append_audit(
            "clone-init-avatar",
            account_alias,
            {
                "clone_id": clone_id,
                "source_peer_id": source.id,
            },
        )
        await cooldown(
            tg(
                functions.channels.EditPhotoRequest(
                    channel=destination,
                    photo=types.InputChatUploadedPhoto(file=uploaded),
                )
            )
        )
    return full_chat


async def _init_discussion(
    tg, destination, clone_state, full_chat, account_alias, clone_id
) -> None:
    """Create/adopt and link the destination discussion group before any post.
    An unreadable source group is not an error: the clone stays posts-only and
    honestly records comments == "unavailable"."""
    if clone_state.source_kind != "broadcast":
        return
    linked = discussion.linked_chat_id(full_chat)
    if linked is None:
        clone_state.comments = "none"
        return state.save(clone_state)
    clone_state.discussion_source_peer_id = linked

    def cooldown(awaitable):
        return _with_cooldown(awaitable, clone_state)

    def mutate(request):
        return _mutate(tg, request, clone_state)

    try:
        source_group = await tg.get_entity(types.PeerChannel(linked))
        await cooldown(tg.get_messages(source_group, limit=1))
    except (
        ValueError,
        telethon_errors.ChannelPrivateError,
        telethon_errors.ChatAdminRequiredError,
    ):
        clone_state.comments = "unavailable"
        return state.save(clone_state)
    clone_state.comments = "enabled"
    state.save(clone_state)
    group = await discussion.adopt(
        tg,
        mutate,
        lambda marker, shape_ok: _marker_candidates(tg, marker, shape_ok),
        f"{clone_state.creation_marker}-discussion",
        clone_state.discussion_destination_peer_id,
        lambda: safety.append_audit(
            "clone-init-discussion-create", account_alias, {"clone_id": clone_id}
        ),
    )
    if not discussion.is_discussion_destination(group):
        raise PolicyError("clone discussion group is not a private owned megagroup")
    clone_state.discussion_destination_peer_id = group.id
    state.save(clone_state)
    title = attribution.display_name(source_group)
    if getattr(group, "title", None) != title:
        await mutate(functions.channels.EditTitleRequest(channel=group, title=title))
        group.title = title
    await _copy_profile(tg, source_group, group, account_alias, clone_id, cooldown)
    safety.append_audit(
        "clone-init-discussion-link", account_alias, {"clone_id": clone_id}
    )
    await discussion.ensure_linked(mutate, destination, group)
    clone_state.discussion_linked = True
    state.save(clone_state)


async def commit_init(tg, source: str, account_alias: str, payload: dict) -> dict:
    entity, source_kind, _ = await _resolve_source(tg, source)
    me = await tg.get_me()
    if (
        me.id != payload["account_user_id"]
        or entity.id != payload["source_peer_id"]
        or payload.get("source_kind", source_kind) != source_kind
    ):
        raise PolicyError("clone init preview no longer matches the source or account")
    clone_id = state.clone_id(me.id, entity.id)
    replace = bool(payload.get("replace"))
    if replace and (
        archived := state.supersede(clone_id, (roster.path_for(clone_id),))
    ):
        safety.append_audit(
            "clone-init-replace",
            account_alias,
            {"clone_id": clone_id, "archived": [path.name for path in archived]},
        )
    try:
        existing = state.load(clone_id)
    except PolicyError as exc:
        raise PolicyError(
            f"{exc}; re-run clone init --replace to supersede it"
        ) from exc
    clone_state = existing or state.CloneState.new(
        account_user_id=me.id,
        source_peer_id=entity.id,
        source_title=payload["source_title"],
        source_kind=payload.get("source_kind", source_kind),
    )
    if clone_state.source_kind != source_kind:
        raise PolicyError("clone source kind no longer matches initialized state")
    if clone_state.creation_marker is None:
        nonce = f"-{secrets.token_hex(3)}" if replace else ""
        clone_state.creation_marker = f"tgcli-clone-{clone_id[:12]}{nonce}"
    marker = clone_state.creation_marker
    state.save(clone_state)
    _enforce_cooldown(clone_state)
    forum = clone_state.destination_kind == "forum"
    shape_ok = topics.is_forum_destination if forum else _is_private_owned_broadcast
    kind_name = "forum megagroup" if forum else "broadcast channel"
    if clone_state.destination_peer_id is not None:
        try:
            destination = await tg.get_entity(
                types.PeerChannel(clone_state.destination_peer_id)
            )
        except ValueError:
            raise PolicyError("clone destination is unavailable") from None
        if not shape_ok(destination):
            raise PolicyError(f"clone destination is not a private owned {kind_name}")
    else:
        valid, wrong_shape = await _marker_candidates(tg, marker, shape_ok)
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
                topics.create_request(marker)
                if forum
                else functions.channels.CreateChannelRequest(
                    title=marker, about="", broadcast=True, megagroup=False
                ),
                clone_state,
            )
            candidates = [
                item
                for item in getattr(update, "chats", ())
                if shape_ok(item, title=marker)
            ]
            if len(candidates) != 1:
                raise PolicyError("Telegram did not return the created private channel")
            destination = candidates[0]
        clone_state.destination_peer_id = destination.id
        state.save(clone_state)
    if forum and not getattr(destination, "forum", False):
        safety.append_audit("clone-init-forum", account_alias, {"clone_id": clone_id})
    if forum:
        await topics.ensure_forum(
            lambda request: _mutate(tg, request, clone_state), destination
        )
    if getattr(destination, "title", None) != clone_state.source_title:
        safety.append_audit("clone-init-title", account_alias, {"clone_id": clone_id})
        await _mutate(
            tg,
            functions.channels.EditTitleRequest(
                channel=destination, title=clone_state.source_title
            ),
            clone_state,
        )
        destination.title = clone_state.source_title
    full_chat = await _copy_profile(
        tg,
        entity,
        destination,
        account_alias,
        clone_id,
        lambda awaitable: _with_cooldown(awaitable, clone_state),
    )
    await _init_discussion(
        tg, destination, clone_state, full_chat, account_alias, clone_id
    )
    return {
        "clone": {
            "id": clone_state.clone_id,
            "source": {
                "id": clone_state.source_peer_id,
                "title": clone_state.source_title,
                "kind": clone_state.source_kind,
            },
            "destination": {
                "id": clone_state.destination_peer_id,
                "title": getattr(destination, "title", clone_state.source_title),
            },
            "comments": clone_state.comments,
            "status": "ready",
            "commit_required": False,
        }
    }


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


async def _uploaded_media(tg, message, path, clone_state):
    input_file = await _with_cooldown(tg.upload_file(str(path)), clone_state)
    if isinstance(message.media, types.MessageMediaPhoto):
        return types.InputMediaUploadedPhoto(file=input_file)
    document = message.media.document
    return types.InputMediaUploadedDocument(
        file=input_file,
        mime_type=getattr(document, "mime_type", None) or "application/octet-stream",
        attributes=list(getattr(document, "attributes", None) or ()),
    )


def _body_text(message, author, plan) -> tuple[str, list | None]:
    return quote_fallback.apply_body(message, author, plan)


async def _reupload_batch(
    tg,
    destination,
    clone_state,
    account_alias,
    messages,
    random_ids,
    reply_to,
    author=None,
    plan=None,
):
    plan = plan or transport.TransportPlan(
        mode="reuploaded", reply_to=reply_to, reply_flattened=False, needs_author=False
    )
    with tempfile.TemporaryDirectory(prefix="tgcli-clone-reupload-") as workdir:
        downloads = {}
        for message in messages:
            media = getattr(message, "media", None)
            if media is None or isinstance(media, types.MessageMediaWebPage):
                continue
            downloaded = await _with_cooldown(
                tg.download_media(message, file=Path(workdir) / f"src-{message.id}"),
                clone_state,
            )
            if downloaded is None:
                raise PolicyError(
                    f"clone media download failed at source message {message.id}"
                )
            downloads[message.id] = Path(downloaded)
        safety.append_audit(
            "clone-sync-reupload",
            account_alias,
            {
                "clone_id": clone_state.clone_id,
                "source_message_ids": [m.id for m in messages],
            },
        )
        if len(messages) == 1:
            message = messages[0]
            media = getattr(message, "media", None)
            text, entities = _body_text(message, author, plan)
            common = {
                "peer": destination,
                "message": text,
                "random_id": random_ids[0],
                "reply_to": reply_to,
                "entities": entities,
            }
            if media is None or isinstance(media, types.MessageMediaWebPage):
                request = functions.messages.SendMessageRequest(
                    **common, no_webpage=media is None
                )
            else:
                request = functions.messages.SendMediaRequest(
                    **common,
                    media=await _uploaded_media(
                        tg, message, downloads[message.id], clone_state
                    ),
                )
            return await _mutate(tg, request, clone_state)
        multi_media = []
        for index, (message, random_id) in enumerate(
            zip(messages, random_ids, strict=True)
        ):
            if message.id not in downloads:
                raise PolicyError(
                    f"clone album item is not reconstructable: {message.id}"
                )
            uploaded = await _uploaded_media(
                tg, message, downloads[message.id], clone_state
            )
            stored = await _mutate(
                tg,
                functions.messages.UploadMediaRequest(peer=destination, media=uploaded),
                clone_state,
            )
            text, entities = _body_text(message, author if index == 0 else None, plan)
            multi_media.append(
                types.InputSingleMedia(
                    media=telethon_utils.get_input_media(stored),
                    random_id=random_id,
                    message=text,
                    entities=entities,
                )
            )
        request = functions.messages.SendMultiMediaRequest(
            peer=destination, multi_media=multi_media, reply_to=reply_to
        )
        return await _mutate(tg, request, clone_state)


def _drops_author(leg, messages) -> bool:
    """A broadcast clone hides the source-forward header on the channel's own
    posts, but a post that is itself a forward keeps drop_author=False so
    Telegram restores its original forward header instead of erasing the origin.
    """
    return leg.source_kind == "broadcast" and not any(
        getattr(message, "fwd_from", None) is not None for message in messages
    )


async def _forward_batch(
    tg,
    source,
    destination,
    clone_state,
    leg,
    account_alias,
    messages,
    me,
    author_cache,
    plan,
    *,
    topic_dest=None,
):
    source_ids = [message.id for message in messages]
    random_ids = [secrets.randbelow(2**63 - 1) + 1 for _ in messages]
    reply_to = plan.reply_to
    if topic_dest is not None:
        reply_to = topics.place(reply_to, topic_dest)
    top_msg_id = None if topic_dest in (None, topics.GENERAL_TOPIC_ID) else topic_dest
    author = None
    if plan.needs_author:
        author = await attribution.author_of(
            tg,
            source,
            messages[0],
            me,
            author_cache,
            lambda awaitable: _with_cooldown(awaitable, clone_state),
        )
    if plan.mode == "snapshots":
        rendered_text, rendered_entities = await snapshot.render(tg, messages[0])
        text, entities = attribution.with_prefix(
            rendered_text,
            rendered_entities,
            plan.body_prefix or "",
            plan.body_prefix_entities,
        )
        text, entities = attribution.prefixed(text, entities, author)
        safety.append_audit(
            "clone-sync-snapshot",
            account_alias,
            {"clone_id": clone_state.clone_id, "source_message_ids": source_ids},
        )
        response = await _mutate(
            tg,
            functions.messages.SendMessageRequest(
                peer=destination,
                message=text,
                random_id=random_ids[0],
                reply_to=reply_to,
                no_webpage=True,
                entities=entities,
            ),
            clone_state,
        )
    elif plan.mode == "forwarded":
        safety.append_audit(
            "clone-sync-forward",
            account_alias,
            {"clone_id": clone_state.clone_id, "source_message_ids": source_ids},
        )
        request = functions.messages.ForwardMessagesRequest(
            from_peer=source,
            id=source_ids,
            random_id=random_ids,
            to_peer=destination,
            drop_author=_drops_author(leg, messages),
            top_msg_id=top_msg_id,
        )
        response = await _mutate(tg, request, clone_state)
    else:
        response = await _reupload_batch(
            tg,
            destination,
            clone_state,
            account_alias,
            messages,
            random_ids,
            reply_to,
            author,
            plan,
        )
    destination_ids = topics.confirmed_destination_ids(response, random_ids)
    for source_id, destination_id in zip(source_ids, destination_ids, strict=True):
        leg.record_mapping(source_id, destination_id)
    leg.cursor = source_ids[-1]
    state.save(clone_state)
    return len(source_ids), plan.mode, plan.reply_flattened, plan.quote_flattened


async def sync_text(
    tg, source: str, account_alias: str, *, limit: int | None = None
) -> dict:
    source_entity, source_kind, _ = await _resolve_source(tg, source)
    me = await tg.get_me()
    clone_state = state.load(state.clone_id(me.id, source_entity.id))
    if clone_state is None or clone_state.destination_peer_id is None:
        raise PolicyError("clone is not initialized; run clone init first")
    if clone_state.source_kind != source_kind:
        raise PolicyError("clone source kind no longer matches initialized state")
    if clone_state.comments == "enabled" and not clone_state.discussion_linked:
        raise PolicyError(
            "clone discussion group is not linked yet; re-run clone init before syncing"
        )
    _enforce_cooldown(clone_state)
    try:
        destination = await tg.get_entity(
            types.PeerChannel(clone_state.destination_peer_id)
        )
    except ValueError:
        raise PolicyError("clone destination is unavailable") from None
    forum = clone_state.destination_kind == "forum"
    valid_destination = (
        (
            topics.is_forum_destination(destination)
            and getattr(destination, "forum", False)
        )
        if forum
        else _is_private_owned_broadcast(destination)
    )
    if not valid_destination:
        kind_name = "forum megagroup" if forum else "broadcast channel"
        raise PolicyError(f"clone destination is not a private owned {kind_name}")
    await discussion.verify_tail(
        tg,
        destination,
        clone_state.max_destination_id(),
        "destination",
        lambda item: (
            getattr(item, "action", None) is not None
            and not (
                isinstance(item.action, types.MessageActionTopicCreate)
                and item.id not in clone_state.topic_map.values()
            )
        ),
    )
    copied = 0
    copied_batches = 0
    skipped_unsupported = []
    transport_counts = {"forwarded": 0, "reuploaded": 0, "snapshots": 0}
    counters = {"topics_created": 0, "skipped_service": 0, "skipped_autoforward": 0}

    def mutate(request):
        return _mutate(tg, request, clone_state)

    reply_flattened = 0
    quote_flattened: list[dict] = []
    author_cache = {}
    more = False
    posts_leg = legs.posts(clone_state)
    resolve_ctx = quotes.ResolveContext(tg=tg, mutate=mutate, destination=destination)

    async def copy_batch(messages, leg, source, dest) -> None:
        nonlocal copied, copied_batches, reply_flattened
        unsupported = [
            {"id": message.id, "kind": kind}
            for message in messages
            if (kind := fidelity.unsupported_kind(message)) is not None
        ]
        if unsupported:
            skipped_unsupported.extend(unsupported)
            leg.cursor = messages[-1].id
            state.save(clone_state)
            return
        plan = transport.decide(messages, leg, source)
        plan = await quotes.resolve(messages, plan, leg, source, resolve_ctx)
        topic_dest = None
        if forum:
            topic_dest = await topics.ensure_topic(
                mutate,
                source,
                dest,
                clone_state,
                topics.topic_id_of(messages[0]),
                counters,
                account_alias=account_alias,
            )
        batch_copied, mode, flattened, flattened_quote = await quotes.send_with_degrade(
            lambda active_plan: _forward_batch(
                tg,
                source,
                dest,
                clone_state,
                leg,
                account_alias,
                list(messages),
                me,
                author_cache,
                active_plan,
                topic_dest=topic_dest,
            ),
            list(messages),
            plan,
            leg,
            source,
            resolve_ctx,
        )
        copied += batch_copied
        transport_counts[mode] += batch_copied
        reply_flattened += int(flattened)
        if flattened_quote is not None:
            quote_flattened.append(flattened_quote)
        copied_batches += 1

    async for event in batching.plan(
        tg.iter_messages(source_entity, min_id=posts_leg.cursor, reverse=True)
    ):
        if limit is not None and copied_batches >= limit:
            more = True
            break
        if isinstance(event, batching.ServiceSkip):
            source_message = event.message
            if forum and isinstance(
                source_message.action, types.MessageActionTopicCreate
            ):
                if clone_state.topic_dest_for(source_message.id) is None:
                    await topics.create_topic(
                        mutate,
                        destination,
                        clone_state,
                        source_message.id,
                        account_alias=account_alias,
                        title=source_message.action.title,
                        icon_color=getattr(source_message.action, "icon_color", None),
                        icon_emoji_id=getattr(
                            source_message.action, "icon_emoji_id", None
                        ),
                    )
                    counters["topics_created"] += 1
            else:
                counters["skipped_service"] += 1
            posts_leg.cursor = event.message_id
            state.save(clone_state)
            continue
        await copy_batch(event.messages, posts_leg, source_entity, destination)
    if clone_state.comments == "enabled" and not more:
        more = await comments.sync_phase(
            tg,
            clone_state,
            source_entity,
            destination,
            mutate,
            copy_batch,
            counters,
            lambda: limit is not None and copied_batches >= limit,
            resolve_ctx,
        )
    participants = await roster.collect(tg, clone_state, source_entity)
    clone_state.last_synced_at = datetime.now(UTC).isoformat()
    state.save(clone_state)
    data = {
        "clone": {
            "id": clone_state.clone_id,
            "source": {
                "id": source_entity.id,
                "title": clone_state.source_title,
                "kind": clone_state.source_kind,
            },
            "destination": {"id": destination.id, "title": destination.title},
        },
        "sync": {
            "copied": copied,
            "skipped_unsupported": skipped_unsupported,
            **transport_counts,
            **counters,
            "reply_flattened": reply_flattened,
            "quote_flattened": quote_flattened,
            "cursor": clone_state.cursor,
            "discussion_cursor": clone_state.discussion_cursor,
            "more": more,
            "participants": participants,
        },
    }
    if quote_flattened:
        raise PartialFailure(
            f"clone sync finished with {len(quote_flattened)} quote fallback(s)",
            data,
            cause=PolicyError("clone planted quote fallback(s)"),
            rows=sync_rows(data),
        )
    return data


def sync_rows(data: dict) -> list[tuple]:
    clone = data["clone"]
    sync = data["sync"]
    return [
        (
            sync["copied"],
            sync["forwarded"],
            sync["reuploaded"],
            sync["snapshots"],
            sync["reply_flattened"],
            len(sync["quote_flattened"]),
            sync["skipped_service"],
            len(sync["skipped_unsupported"]),
            sync["topics_created"],
            sync["cursor"],
            clone["id"],
            clone["source"]["id"],
            clone["destination"]["id"],
            sync["more"],
            sync["skipped_autoforward"],
            sync["discussion_cursor"],
        )
    ]
