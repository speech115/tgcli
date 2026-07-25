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
    ergonomics,
    fidelity,
    flood,
    legs,
    quote_fallback,
    quotes,
    reforward,
    roster,
    snapshot,
    state,
    topics,
    transport,
)
from tgcli.errors import NotFoundError, PartialFailure, PolicyError, RateLimitError
from tgcli.output import note
from tgcli.transfer import (
    CHUNK_SIZE,
    CLONE_TRANSFER_PARALLEL,
    download_striped,
    media_byte_size,
    upload_parts,
)


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
            if not path.name.startswith("account-")
            and (entry := _load_entry(path, source)) is not None
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


async def preview_init(
    tg, source: str, *, replace: bool = False, no_comments: bool = False
) -> dict:
    entity, source_kind, source_title = await _resolve_source(tg, source)
    me = await tg.get_me()
    total = (await tg.get_messages(entity, limit=0)).total
    clone_id = state.clone_id(me.id, entity.id)
    peers_to_create = await _peers_to_create(
        tg,
        entity,
        source_kind,
        clone_id,
        no_comments=no_comments,
        replace=replace,
    )
    account_flood = flood.load(me.id)
    preview = safety.create_preview(
        {
            "kind": "clone-init",
            "source": source,
            "account_user_id": me.id,
            "source_peer_id": entity.id,
            "source_title": source_title,
            "source_kind": source_kind,
            "replace": replace,
            "no_comments": no_comments,
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
        "peers_to_create": peers_to_create,
        "account_flood": account_flood,
    }


async def _peers_to_create(
    tg, entity, source_kind, clone_id, *, no_comments: bool, replace: bool = False
) -> int:
    try:
        existing = state.load(clone_id)
    except PolicyError:
        existing = None
    if (
        not replace
        and existing is not None
        and existing.destination_peer_id is not None
    ):
        return 0
    if no_comments or source_kind != "broadcast":
        return 1
    full = await tg(functions.channels.GetFullChannelRequest(entity))
    return 2 if discussion.linked_chat_id(full.full_chat) is not None else 1


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


def _raise_if_cooling(deadline: datetime) -> None:
    retry_after = ceil((deadline - datetime.now(UTC)).total_seconds())
    if retry_after > 0:
        raise RateLimitError(
            f"rate limited for {retry_after}s", retry_after=retry_after
        )


def _enforce_account_cooldown(account_user_id: int) -> None:
    deadline = flood.cooldown_deadline(account_user_id)
    if deadline is not None:
        _raise_if_cooling(deadline)


def _enforce_cooldown(clone_state: state.CloneState) -> None:
    deadlines = [
        deadline
        for deadline in (
            clone_state.cooldown_deadline(),
            flood.cooldown_deadline(clone_state.account_user_id),
        )
        if deadline is not None
    ]
    if deadlines:
        _raise_if_cooling(max(deadlines))


async def _with_cooldown(awaitable, clone_state):
    try:
        return await awaitable
    except telethon_errors.FloodWaitError as exc:
        deadline = datetime.now(UTC) + timedelta(seconds=exc.seconds)
        clone_state.set_cooldown(deadline)
        state.save(clone_state)
        flood.arm_cooldown(clone_state.account_user_id, deadline)
        raise


async def _mutate(tg, request, clone_state: state.CloneState):
    result = await _with_cooldown(tg(request), clone_state)
    if isinstance(request, functions.channels.CreateChannelRequest):
        flood.record_peer_created(clone_state.account_user_id, datetime.now(UTC))
    return result


async def _copy_profile(tg, source, destination, account_alias, clone_state, cooldown):
    """Copies about/avatar onto destination; returns the source's full chat.

    The avatar copy is idempotent: the copied source photo id is recorded in
    clone state, so an init re-run skips the download/upload/EditPhoto chain
    (and its service message) until the source avatar actually changes.
    """
    clone_id = clone_state.clone_id
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
    photo_id = getattr(photo, "photo_id", None)
    if photo_id is not None and clone_state.avatar_for(source.id) == photo_id:
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
        if photo_id is not None:
            clone_state.record_avatar(source.id, photo_id)
            state.save(clone_state)
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
    title = attribution.destination_title(attribution.display_name(source_group))
    if getattr(group, "title", None) != title:
        await mutate(functions.channels.EditTitleRequest(channel=group, title=title))
        group.title = title
    await _copy_profile(tg, source_group, group, account_alias, clone_state, cooldown)
    safety.append_audit(
        "clone-init-discussion-link", account_alias, {"clone_id": clone_id}
    )
    await discussion.ensure_linked(mutate, destination, group)
    clone_state.discussion_linked = True
    state.save(clone_state)


async def commit_init(tg, source: str, account_alias: str, payload: dict) -> dict:
    # Local cooldown gate first — CONTRACT/ADR-0045: no Telegram traffic while
    # an account (or existing per-clone) deadline is active. Preview payload
    # already carries account_user_id / source_peer_id.
    account_user_id = payload["account_user_id"]
    source_peer_id = payload["source_peer_id"]
    early_id = state.clone_id(account_user_id, source_peer_id)
    try:
        early_state = state.load(early_id)
    except PolicyError:
        early_state = None
    if early_state is not None:
        _enforce_cooldown(early_state)
    else:
        _enforce_account_cooldown(account_user_id)

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
    no_comments = bool(payload.get("no_comments")) or clone_state.comments == "disabled"
    if no_comments and clone_state.comments == "enabled":
        raise PolicyError(
            "clone init --no-comments cannot disable an existing linked discussion; "
            "re-run with --replace to start a fresh posts-only clone"
        )
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
    titled = attribution.destination_title(clone_state.source_title)
    if getattr(destination, "title", None) != titled:
        safety.append_audit("clone-init-title", account_alias, {"clone_id": clone_id})
        await _mutate(
            tg,
            functions.channels.EditTitleRequest(channel=destination, title=titled),
            clone_state,
        )
        destination.title = titled
    full_chat = await _copy_profile(
        tg,
        entity,
        destination,
        account_alias,
        clone_state,
        lambda awaitable: _with_cooldown(awaitable, clone_state),
    )
    if no_comments:
        clone_state.comments = "disabled"
        state.save(clone_state)
    else:
        await _init_discussion(
            tg, destination, clone_state, full_chat, account_alias, clone_id
        )
    peers = [destination]
    discussion_unresolved = False
    if clone_state.discussion_destination_peer_id is not None:
        try:
            peers.append(
                await tg.get_entity(
                    types.PeerChannel(clone_state.discussion_destination_peer_id)
                )
            )
        except (ValueError, telethon_errors.RPCError):
            discussion_unresolved = True
            note(
                "warning: clone mute skipped for discussion peer "
                f"{clone_state.discussion_destination_peer_id}: unresolved"
            )
    applied = await ergonomics.apply(tg, peers)
    if discussion_unresolved:
        applied["muted"] = False
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
                "title": getattr(destination, "title", titled),
            },
            "comments": clone_state.comments,
            "status": "ready",
            "commit_required": False,
        },
        "ergonomics": applied,
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
    async def invoke(awaitable):
        return await _with_cooldown(awaitable, clone_state)

    input_file = await upload_parts(
        tg,
        path,
        parallel=CLONE_TRANSFER_PARALLEL,
        invoke=invoke,
    )
    if isinstance(message.media, types.MessageMediaPhoto):
        return types.InputMediaUploadedPhoto(file=input_file)
    document = message.media.document
    return types.InputMediaUploadedDocument(
        file=input_file,
        mime_type=getattr(document, "mime_type", None) or "application/octet-stream",
        attributes=list(getattr(document, "attributes", None) or ()),
    )


async def _download_for_reupload(tg, message, workdir: Path, clone_state) -> Path:
    target = workdir / f"src-{message.id}"
    size = media_byte_size(message)
    if size is not None and size > CHUNK_SIZE:
        await _with_cooldown(
            download_striped(
                tg,
                message.media,
                target,
                size=size,
                parallel=CLONE_TRANSFER_PARALLEL,
            ),
            clone_state,
        )
        return target
    downloaded = await _with_cooldown(
        tg.download_media(message, file=target),
        clone_state,
    )
    if downloaded is None:
        raise PolicyError(f"clone media download failed at source message {message.id}")
    return Path(downloaded)


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
            downloads[message.id] = await _download_for_reupload(
                tg, message, Path(workdir), clone_state
            )
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
    poll_votes: list | None = None,
    reforward_cache: dict | None = None,
):
    source_ids = [message.id for message in messages]
    random_ids = [secrets.randbelow(2**63 - 1) + 1 for _ in messages]
    reply_to = plan.reply_to
    if topic_dest is not None:
        reply_to = topics.place(reply_to, topic_dest)
    top_msg_id = None if topic_dest in (None, topics.GENERAL_TOPIC_ID) else topic_dest

    async def cooldown(awaitable):
        return await _with_cooldown(awaitable, clone_state)

    # ADR-0050 Part B: a proven original outranks the Part A prefix, because
    # forwarding it carries Telegram's own header instead of describing one.
    proven = None
    if reforward_cache is not None and reforward.eligible(leg, messages, plan):
        proven = await reforward.locate(
            tg, clone_state, messages[0], reforward_cache, invoke=cooldown
        )
    author = None
    if plan.needs_author and proven is None:
        if (
            leg.source_kind == "broadcast"
            and getattr(messages[0], "fwd_from", None) is not None
        ):
            author = await attribution.forwarded_author_of(
                tg, messages[0], author_cache, cooldown
            )
        else:
            author = await attribution.author_of(
                tg, source, messages[0], me, author_cache, cooldown
            )
    mode = plan.mode
    if proven is not None:
        group, group_message_id = proven
        mode = "forwarded"
        safety.append_audit(
            "clone-sync-reforward",
            account_alias,
            {
                "clone_id": clone_state.clone_id,
                "source_message_ids": source_ids,
                "group_message_ids": [group_message_id],
            },
        )
        response = await _mutate(
            tg,
            functions.messages.ForwardMessagesRequest(
                from_peer=group,
                id=[group_message_id],
                random_id=random_ids,
                to_peer=destination,
                drop_author=False,
                top_msg_id=top_msg_id,
            ),
            clone_state,
        )
    elif plan.mode == "snapshots":
        rendered_text, rendered_entities, poll_marker = await snapshot.render(
            tg,
            messages[0],
            peer=source,
            account_alias=account_alias,
            invoke=lambda awaitable: _with_cooldown(awaitable, clone_state),
        )
        if poll_marker is not None and poll_votes is not None:
            poll_votes.append(poll_marker)
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
    return len(source_ids), mode, plan.reply_flattened, plan.quote_flattened


async def sync_text(
    tg, source: str, account_alias: str, *, limit: int | None = None
) -> dict:
    # Account identity is local-session-bound via get_me; enforce the account
    # cooldown before username/entity resolve so a hot account never hits
    # further Telegram RPCs (ADR-0045). Per-clone deadline is checked after
    # state load below.
    me = await tg.get_me()
    _enforce_account_cooldown(me.id)
    source_entity, source_kind, _ = await _resolve_source(tg, source)
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
    poll_votes: list[dict] = []
    author_cache = {}
    reforward_cache: dict = {}
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
                poll_votes=poll_votes,
                reforward_cache=reforward_cache,
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
            "poll_votes": poll_votes,
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
