"""Copy supported Telegram chats into user-owned channels (ADR-0017/0021)."""

import secrets
import shutil
from datetime import UTC, datetime

from telethon import errors as telethon_errors, utils as telethon_utils
from telethon.errors import MessageNotModifiedError
from telethon.tl import functions, types

from tgcli import chatref, safety
from tgcli.clone import (
    attribution,
    batching,
    comments,
    cooldown as cooldown_mod,
    discussion,
    ergonomics,
    fidelity,
    init_peers,
    legs,
    lookup,
    pin,
    progress as clone_progress,
    quote_fallback,
    quotes,
    reforward,
    refresh as clone_refresh,
    reupload,
    roster,
    snapshot,
    state,
    topics,
    transport,
)
from tgcli.errors import NotFoundError, PartialFailure, PolicyError
from tgcli.governor import pacing
from tgcli.output import note


def _entry(s: state.CloneState) -> dict:
    probe = state.probe(s.clone_id)
    return {
        "clone_id": s.clone_id,
        "source": {
            "id": s.source_peer_id,
            "title": s.source_title,
            "kind": s.source_kind,
        },
        "destination": {
            "id": s.destination_peer_id,
            "title": s.destination_title,
            "username": s.destination_username,
        },
        "cursor": s.cursor,
        "copied": len(s.id_map),
        "cooldown_until": s.retry_not_before,
        "created_at": s.created_at,
        "last_synced_at": s.last_synced_at,
        "comments": s.comments,
        "discussion_linked": s.discussion_linked,
        "schema_version": probe["schema_version"],
        "integrity": probe["integrity"],
    }


def half_initialized(entry: dict) -> bool:
    """Comments were planned for this clone but its group was never linked.

    A `clone init --commit` that floods on `SetDiscussionGroup` creates the
    destination and stops there; `clone sync` then refuses and nothing in the
    listing said why (#170).
    """
    return entry["comments"] == "enabled" and entry["discussion_linked"] is False


def _unreadable_entry(clone_id: str) -> dict:
    probe = state.probe(clone_id)
    return {
        "clone_id": clone_id,
        "source": {"id": None, "title": None, "kind": None},
        "destination": {"id": None, "title": None, "username": None},
        "cursor": None,
        "copied": None,
        "cooldown_until": None,
        "created_at": None,
        "last_synced_at": None,
        "comments": None,
        "discussion_linked": None,
        "schema_version": probe["schema_version"],
        "integrity": probe["integrity"],
        "unreadable": True,
    }


# A `.json` slot with no `.db` beside it that `state.load` still refused: a
# pre-v2 document, or one too broken to parse. Nothing in it can be read, so
# its entry is `clone_id` plus nulls — ten of those bury the real clones
# (#173). They are counted, not listed, unless `--all` asks for them.
PENDING_IMPORT = "json-pending-import"


def list_clones(source: str | None = None, *, include_all: bool = False) -> dict:
    directory = state.clones_dir()
    if not directory.exists():
        return {"clones": [], "pending_import": 0}
    clone_ids = lookup.slot_ids()
    entries = []
    pending_import = 0
    for clone_id in clone_ids:
        try:
            loaded = state.load(clone_id)
        except PolicyError:
            entry = _unreadable_entry(clone_id)
            if entry["integrity"] == PENDING_IMPORT:
                # Counted whatever the filter is: the slot exists on disk
                # either way, and its identity can never match a filter.
                pending_import += 1
                if not include_all:
                    continue
            if source is None:
                entries.append(entry)
            continue
        if loaded is None or not lookup.matches(loaded, source):
            continue
        entries.append(_entry(loaded))
    # An unreadable entry has no created_at at all (CONTRACT §11: every field
    # but clone_id is null), so it sorts ahead of every dated one.
    entries.sort(key=lambda entry: entry["created_at"] or "")
    return {"clones": entries, "pending_import": pending_import}


def export_state(source: str) -> dict:
    """Readonly rollback/diagnostic path: the v2 JSON document for one clone."""
    matches = [
        loaded for loaded in lookup.loaded_states() if lookup.matches(loaded, source)
    ]
    if not matches:
        raise PolicyError(f"clone not found: {source!r}")
    if len(matches) > 1:
        raise PolicyError(
            f"clone export-state matched {len(matches)} clones for {source!r}; "
            "narrow the source filter"
        )
    return matches[0].to_dict()


def progress_token(account_user_id: int, source: str) -> dict:
    """Durable cursors for the account-owned clone selected by SOURCE."""
    clones = sorted(
        (
            loaded
            for loaded in lookup.loaded_states()
            if loaded.account_user_id == account_user_id
            and lookup.matches(loaded, source)
        ),
        key=lambda loaded: loaded.source_peer_id,
    )
    return {
        "clones": [
            {
                "source_peer_id": found.source_peer_id,
                "cursor": found.cursor,
                "discussion_cursor": found.discussion_cursor,
                "copied": len(found.id_map),
                "discussion_copied": len(found.discussion_id_map),
            }
            for found in clones
        ]
    }


def status_rows(data: dict) -> list[tuple]:
    return [
        (
            c["source"]["id"],
            c["clone_id"] if c.get("unreadable") else c["source"]["title"],
            c["source"]["kind"],
            c["destination"]["title"] or c["destination"]["id"],
            c["cursor"],
            c["copied"],
            c["last_synced_at"],
            "unreadable" if c.get("unreadable") else c["comments"],
        )
        for c in data["clones"]
    ]


# What Telegram answers when the recorded destination cannot be opened by this
# account any more — deleted, left, or banned. ``channels.GetChannels`` raises
# these; Telethon's own ValueError covers a peer it cannot resolve at all.
DESTINATION_UNAVAILABLE = discussion.PEER_UNAVAILABLE


async def _resolve_destination(tg, destination_peer_id: int):
    try:
        return await tg.get_entity(types.PeerChannel(destination_peer_id))
    except DESTINATION_UNAVAILABLE:
        raise PolicyError("clone destination is unavailable") from None


def _record_destination_name(clone_state, destination) -> None:
    """Remember what the destination is called, for the offline status listing.

    A clone destination is private and unnamed to everyone but its creator, so
    `clone status` could only ever print a bare peer id (#175). Every command
    that resolves the peer refreshes the recorded name; the caller saves.
    """
    clone_state.destination_title = getattr(destination, "title", None)
    clone_state.destination_username = getattr(destination, "username", None)


async def _resolve_source(tg, source: str, *, account_user_id: int | None = None):
    """Resolve SOURCE to an entity. Pass ``account_user_id`` where a clone must
    already exist, so a title can be answered from state instead of Telegram."""
    ref = chatref.parse(source)
    if account_user_id is not None and isinstance(ref, str):
        ref = lookup.recorded_source_ref(account_user_id, source) or ref
    try:
        entity = await tg.get_entity(ref)
    except DESTINATION_UNAVAILABLE:
        # ValueError plus the peer-refusal family: from here they all mean the
        # same operable thing — this account cannot open that source.
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


# The cooldown gate and the RPC seam now live in clone/cooldown.py; these
# names stay as the command surface's local vocabulary.
_enforce_cooldown = cooldown_mod.enforce
_mutate = cooldown_mod.mutate


# Destination shape, marker adoption and profile copy now live in
# clone/init_peers.py; these names stay as the command surface's vocabulary.
_is_private_owned_broadcast = init_peers.is_private_owned_broadcast
_marker_candidates = init_peers.marker_candidates
_copy_profile = init_peers.copy_profile
_init_discussion = init_peers.init_discussion


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
        destination = await _resolve_destination(tg, clone_state.destination_peer_id)
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
        await topics.ensure_forum(lambda request: _mutate(tg, request), destination)
    titled = attribution.destination_title(clone_state.source_title)
    if getattr(destination, "title", None) != titled:
        safety.append_audit("clone-init-title", account_alias, {"clone_id": clone_id})
        await _mutate(
            tg,
            functions.channels.EditTitleRequest(channel=destination, title=titled),
        )
        destination.title = titled
    _record_destination_name(clone_state, destination)
    state.save(clone_state)
    full_chat = await _copy_profile(
        tg,
        entity,
        destination,
        account_alias,
        clone_state,
        lambda make_awaitable: make_awaitable(),
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
        except telethon_errors.FloodWaitError:
            # CONTRACT §4: a genuine flood is exit 5 with retry_after, never a
            # quiet "muted: false" on an otherwise successful init.
            raise
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


# The reupload transfer mechanics now live in clone/reupload.py; these names
# stay as the command surface's local vocabulary.
_document_thumb = reupload.document_thumb
_uploaded_thumb = reupload.uploaded_thumb
_uploaded_media = reupload.uploaded_media
_media_cache_dir = reupload.cache_dir
_complete_marker = reupload.complete_marker
_download_checkpoint = reupload.download_checkpoint
_download_for_reupload = reupload.download_for_reupload


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
    progress=None,
):
    plan = plan or transport.TransportPlan(
        mode="reuploaded", reply_to=reply_to, reply_flattened=False, needs_author=False
    )
    cache = _media_cache_dir(clone_state)
    cache.mkdir(parents=True, exist_ok=True)
    downloads = {}
    for message in messages:
        media = getattr(message, "media", None)
        if media is None or isinstance(media, types.MessageMediaWebPage):
            continue
        downloads[message.id] = await _download_for_reupload(
            tg, message, cache, clone_state, progress
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
                    tg, message, downloads[message.id], progress
                ),
            )
        response = await _mutate(tg, request)
    else:
        multi_media = []
        for index, (message, random_id) in enumerate(
            zip(messages, random_ids, strict=True)
        ):
            if message.id not in downloads:
                raise PolicyError(
                    f"clone album item is not reconstructable: {message.id}"
                )
            uploaded = await _uploaded_media(
                tg, message, downloads[message.id], progress
            )
            stored = await _mutate(
                tg,
                functions.messages.UploadMediaRequest(peer=destination, media=uploaded),
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
        response = await _mutate(tg, request)
    # Only a successful send clears the cache — a FloodWait mid-upload must
    # leave downloaded bytes for the next invocation (ADR-0052).
    shutil.rmtree(cache, ignore_errors=True)
    return response


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
    progress=None,
    reforward_cache: dict | None = None,
):
    source_ids = [message.id for message in messages]
    random_ids = [secrets.randbelow(2**63 - 1) + 1 for _ in messages]
    reply_to = plan.reply_to
    if topic_dest is not None:
        reply_to = topics.place(reply_to, topic_dest)
    top_msg_id = None if topic_dest in (None, topics.GENERAL_TOPIC_ID) else topic_dest

    async def cooldown(make_awaitable):
        return await make_awaitable()

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
        )
    elif plan.mode == "snapshots":
        rendered_text, rendered_entities, poll_marker = await snapshot.render(
            tg,
            messages[0],
            peer=source,
            account_alias=account_alias,
            invoke=lambda make_awaitable: make_awaitable(),
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
        response = await _mutate(tg, request)
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
            progress,
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
    # Account identity is local-session-bound; enforce the account cooldown
    # before the get_me RPC and the username/entity resolve so a hot account
    # never hits Telegram at all (ADR-0045). Per-clone deadline is checked
    # after state load below.
    me = await tg.get_me()
    source_entity, source_kind, _ = await _resolve_source(
        tg, source, account_user_id=me.id
    )
    clone_state = state.load(state.clone_id(me.id, source_entity.id))
    if clone_state is None or clone_state.destination_peer_id is None:
        raise PolicyError("clone is not initialized; run clone init first")
    if clone_state.source_kind != source_kind:
        raise PolicyError("clone source kind no longer matches initialized state")
    if clone_state.comments == "enabled" and not clone_state.discussion_linked:
        raise PolicyError(
            "clone discussion group is not linked yet; finish the clone with: "
            f"tg clone init {source} (then --commit the preview it prints)"
        )
    _enforce_cooldown(clone_state)
    # Warn before the work, not only after it: a run killed by FloodWait never
    # reaches the tail, so the resume is where the operator sees this.
    warned_unstarted = clone_progress.comments_unstarted(clone_state)
    destination = await _resolve_destination(tg, clone_state.destination_peer_id)
    _record_destination_name(clone_state, destination)
    state.save(clone_state)
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
        return _mutate(tg, request)

    def cap_exhausted() -> bool:
        """--max-runtime hit: stop normally, keep the cursor for resume."""
        remaining = pacing.wall_clock_remaining()
        return remaining is not None and remaining <= 0

    reply_flattened = 0
    quote_flattened: list[dict] = []
    markup_dropped: list[dict] = []
    poll_votes: list[dict] = []
    author_cache = {}
    reforward_cache: dict = {}
    more = False
    posts_leg = legs.posts(clone_state)
    resolve_ctx = quotes.ResolveContext(tg=tg, mutate=mutate, destination=destination)
    progress = clone_progress.SyncProgress(
        source_entity.id, copied=len(clone_state.id_map)
    )
    posts_exhausted = False

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
            for item in unsupported:
                note(
                    f"warning: source message {item['id']} skipped unsupported "
                    f"{item['kind']}"
                )
            return
        # `source` is this leg's own entity — the channel for posts, the
        # discussion group for comments — so the ~total describes the work
        # the leg's counter is counting (#174).
        await progress.resolve_total(
            tg,
            source,
            lambda make_awaitable: make_awaitable(),
        )
        plan = transport.decide(
            messages,
            leg,
            source,
            posts_cursor=clone_state.cursor,
            posts_exhausted=posts_exhausted,
        )
        if plan.mode == "deferred":
            return
        plan = await quotes.resolve(
            messages,
            plan,
            leg,
            source,
            resolve_ctx,
            posts_cursor=clone_state.cursor,
            posts_exhausted=posts_exhausted,
        )
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
                progress=progress,
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
        # Only a native forward carries a keyboard; every other transport
        # rebuilds the message and Telegram will not let a user account
        # reattach one, so the rows are recorded as lost (ADR-0085). Every
        # loss is announced as it happens, not in the tail: the copy is
        # already permanent and never revisited, and the run that dies on a
        # flood mid-way leaves no result document to carry the rest.
        if mode != "forwarded":
            for message in messages:
                buttons = fidelity.dropped_buttons(message)
                if buttons is None:
                    continue
                note(
                    f"warning: source message {message.id} lost {len(buttons)} "
                    "bot button(s); a keyboard belongs to the bot that "
                    "attached it and no copy can recreate one"
                )
                markup_dropped.append({"id": message.id, "buttons": buttons})
        reply_flattened += int(flattened)
        if flattened_quote is not None:
            quote_flattened.append(flattened_quote)
            note(
                f"warning: source message {flattened_quote['id']} planted quote "
                f"fallback ({flattened_quote['reason']})"
            )
        copied_batches += 1
        progress.batch(batch_copied, mode)

    async def run_posts_window(max_batches: int | None) -> int:
        """Copy up to max_batches posts (None = exhaust). Sets more on --limit."""
        nonlocal more
        ran = 0
        async for event in batching.plan(
            tg.iter_messages(source_entity, min_id=posts_leg.cursor, reverse=True)
        ):
            if limit is not None and copied_batches >= limit:
                more = True
                break
            if cap_exhausted():
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
                            icon_color=getattr(
                                source_message.action, "icon_color", None
                            ),
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
            ran += 1
            if max_batches is not None and ran >= max_batches:
                break
        return ran

    if clone_state.comments == "enabled":
        # ADR-0051: alternate posts×WINDOW with comments until both exhaust.
        # Enter comments only while --limit budget remains; a limit hit during
        # posts ends the run (same as today for limit < WINDOW).
        while not more and not cap_exhausted():
            # Each interleaved window re-announces its leg so the counters
            # belong to the leg that is running (ADR-0051 interleave, #174).
            progress.phase("posts", copied=len(clone_state.id_map))
            ran = await run_posts_window(legs.WINDOW)
            if more or cap_exhausted():
                break
            posts_exhausted = ran < legs.WINDOW
            progress.phase("comments", copied=len(clone_state.discussion_id_map))
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
                posts_exhausted=posts_exhausted,
            )
            if posts_exhausted:
                break
    else:
        await run_posts_window(None)

    # ADR-0055: pin carry-over is broadcast-only; forum sync JSON omits `pinned`.
    pinned_result = None
    if not forum:
        if not more:
            pinned_result = await pin.sync_phase(
                tg,
                clone_state,
                source_entity,
                destination,
                mutate,
                lambda make: make(),
                account_alias,
            )
        else:
            pinned_result = pin.snapshot(clone_state)
    if not warned_unstarted:
        clone_progress.comments_unstarted(clone_state)
    progress.phase("roster")
    participants = await roster.collect(tg, clone_state, source_entity)
    clone_state.last_synced_at = datetime.now(UTC).isoformat()
    state.save(clone_state)
    data: dict = {
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
            "markup_dropped": markup_dropped,
            "poll_votes": poll_votes,
            "cursor": clone_state.cursor,
            "discussion_cursor": clone_state.discussion_cursor,
            "more": more,
            "participants": participants,
            **({"pinned": pinned_result} if pinned_result is not None else {}),
        },
    }
    data["remaining"] = bool(more or cap_exhausted())
    if quote_flattened:
        raise PartialFailure(
            f"clone sync finished with {len(quote_flattened)} quote fallback(s)",
            data,
            cause=PolicyError("clone planted quote fallback(s)"),
            rows=sync_rows(data),
        )
    if cap_exhausted() and not more:
        # --max-runtime exhausted: a normal stop, not an error — the cursor
        # advanced and the next invocation resumes (ADR-0072 decision 6).
        data["stop_reason"] = "wall_clock_cap"  # type: ignore[index]
        data["resume"] = {"cursor": clone_state.cursor}
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
            len(sync["markup_dropped"]),
        )
    ]


async def _load_refresh_context(tg, source: str):
    me = await tg.get_me()
    source_entity, source_kind, _ = await _resolve_source(
        tg, source, account_user_id=me.id
    )
    clone_state = state.load(state.clone_id(me.id, source_entity.id))
    if clone_state is None or clone_state.destination_peer_id is None:
        raise PolicyError("clone is not initialized; run clone init first")
    if clone_state.source_kind != source_kind:
        raise PolicyError("clone source kind no longer matches initialized state")
    _enforce_cooldown(clone_state)
    destination = await _resolve_destination(tg, clone_state.destination_peer_id)
    return me, source_entity, destination, clone_state


async def preview_refresh(tg, source: str) -> dict:
    me, source_entity, destination, clone_state = await _load_refresh_context(
        tg, source
    )

    async def cooldown(make_awaitable):
        return await make_awaitable()

    eligible, excluded = await clone_refresh.candidates(
        tg, clone_state, source_entity, destination, me, cooldown
    )
    pairs = [
        {"source_id": item.source_id, "destination_id": item.destination_id}
        for item in eligible
    ]
    preview = safety.create_preview(
        {
            "kind": "clone-refresh",
            "source": source,
            "account_user_id": me.id,
            "source_peer_id": source_entity.id,
            "eligible": pairs,
        }
    )
    return {
        "preview_id": preview["preview_id"],
        "expires_at": preview["expires_at"],
        "clone": {
            "id": clone_state.clone_id,
            "source": {
                "id": source_entity.id,
                "title": clone_state.source_title,
                "kind": clone_state.source_kind,
            },
        },
        "refresh": {
            "eligible": pairs,
            "excluded": [
                {"source_id": item.source_id, "reason": item.reason}
                for item in excluded
            ],
        },
    }


async def commit_refresh(tg, source: str, account_alias: str, payload: dict) -> dict:
    me, source_entity, destination, clone_state = await _load_refresh_context(
        tg, source
    )
    if (
        me.id != payload["account_user_id"]
        or source_entity.id != payload["source_peer_id"]
    ):
        raise PolicyError(
            "clone refresh preview no longer matches the source or account"
        )
    eligible = list(payload.get("eligible") or ())
    for pair in eligible:
        if clone_state.dest_for(int(pair["source_id"])) != int(pair["destination_id"]):
            raise PolicyError(
                "clone refresh preview no longer matches the current id_map"
            )
    input_peer = await tg.get_input_entity(destination)

    async def cooldown(make_awaitable):
        return await make_awaitable()

    author_cache: dict = {}
    edited: list[dict] = []
    skipped: list[dict] = []
    for pair in eligible:
        source_id = pair["source_id"]
        destination_id = pair["destination_id"]
        source_msgs = await cooldown(
            lambda: tg.get_messages(source_entity, ids=[source_id])
        )
        dest_msgs = await cooldown(
            lambda: tg.get_messages(destination, ids=[destination_id])
        )
        message = source_msgs[0] if source_msgs else None
        dest = dest_msgs[0] if dest_msgs else None
        if message is None or dest is None:
            skipped.append({"source_id": source_id, "reason": "not-eligible"})
            continue
        (
            rendered_text,
            rendered_entities,
        ) = await clone_refresh.render_with_current_rules(
            tg,
            source_entity,
            message,
            me,
            clone_state.source_kind,
            author_cache,
            cooldown,
        )
        dest_text = getattr(dest, "message", None) or ""
        dest_entities = getattr(dest, "entities", None)
        if not clone_refresh.eligible_for_backfill(
            message, dest_text, dest_entities, rendered_text, rendered_entities
        ):
            skipped.append({"source_id": source_id, "reason": "not-eligible"})
            continue
        safety.append_audit(
            "clone-refresh-prefix",
            account_alias,
            {
                "clone_id": clone_state.clone_id,
                "source_message_id": source_id,
                "destination_message_id": destination_id,
            },
        )
        try:
            await _mutate(
                tg,
                functions.messages.EditMessageRequest(
                    peer=input_peer,
                    id=destination_id,
                    message=rendered_text,
                    entities=rendered_entities,
                ),
            )
        except MessageNotModifiedError:
            pass
        edited.append({"source_id": source_id, "destination_id": destination_id})
    return {
        "clone": {
            "id": clone_state.clone_id,
            "source": {
                "id": source_entity.id,
                "title": clone_state.source_title,
                "kind": clone_state.source_kind,
            },
            "destination": {"id": destination.id, "title": destination.title},
        },
        "refresh": {
            "edited": edited,
            "skipped": skipped,
            "count": len(edited),
        },
    }


def refresh_rows(data: dict) -> list[tuple]:
    refresh = data["refresh"]
    if "edited" in refresh:
        rows = [
            (item["source_id"], item["destination_id"], "edited")
            for item in refresh["edited"]
        ]
        rows.extend(
            (item["source_id"], item.get("destination_id"), item["reason"])
            for item in refresh["skipped"]
        )
        return rows
    rows = [
        (item["source_id"], item["destination_id"], "eligible")
        for item in refresh["eligible"]
    ]
    rows.extend(
        (item["source_id"], None, item["reason"]) for item in refresh["excluded"]
    )
    return rows
