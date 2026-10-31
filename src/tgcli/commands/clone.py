"""Copy supported Telegram chats into user-owned channels (ADR-0017/0021)."""

import secrets

from telethon import errors as telethon_errors
from telethon.tl import functions, types

from tgcli import safety
from tgcli.clone import (
    attribution,
    discussion,
    ergonomics,
    init_peers,
    lookup,
    resolve,
    roster,
    state,
    topics,
)
from tgcli.errors import PolicyError
from tgcli.output import note
from tgcli.session import TRANSIENT_ERRORS


def _entry(s: state.CloneState, slot_id: str) -> dict:
    """``slot_id`` is the file the state was read from: a pre-ADR-0103 slot
    keeps its kind-blind name until the next init/sync migrates it."""
    probe = state.probe(slot_id)
    return {
        "clone_id": slot_id,
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
        "cooldown_until": lookup.cooldown_until(s.account_user_id),
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


def list_clones(source: str | None = None) -> dict:
    entries = []
    for clone_id in lookup.slot_ids():
        try:
            loaded = state.load(clone_id)
        except PolicyError:
            if source is None:
                entries.append(_unreadable_entry(clone_id))
            continue
        if loaded is None or not lookup.matches(loaded, source):
            continue
        entries.append(_entry(loaded, clone_id))
    # An unreadable entry has no created_at at all (CONTRACT §11: every field
    # but clone_id is null), so it sorts ahead of every dated one.
    entries.sort(key=lambda entry: entry["created_at"] or "")
    return {"clones": entries}


export_state = lookup.export_state


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
    entity, source_kind, source_title = await resolve.resolve_source(tg, source)
    me = await tg.get_me()
    total = (await tg.get_messages(entity, limit=0)).total
    clone_id = state.resolve_slot(me.id, entity.id, source_kind)
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


async def commit_init(tg, source: str, account_alias: str, payload: dict) -> dict:
    entity, source_kind, _ = await resolve.resolve_source(tg, source)
    me = await tg.get_me()
    if (
        me.id != payload["account_user_id"]
        or entity.id != payload["source_peer_id"]
        or payload.get("source_kind", source_kind) != source_kind
    ):
        raise PolicyError("clone init preview no longer matches the source or account")
    with state.slot_lock(state.clone_id(me.id, entity.id, source_kind)):
        return await _commit_slot(tg, entity, source_kind, me, account_alias, payload)


async def _commit_slot(tg, entity, source_kind, me, account_alias, payload) -> dict:
    replace = bool(payload.get("replace"))
    clone_id = state.resolve_slot(me.id, entity.id, source_kind)
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
    paused = clone_state.comments == "none" and clone_state.discussion_linked
    if no_comments and (clone_state.comments == "enabled" or paused):
        raise PolicyError(
            "clone init --no-comments cannot disable an existing linked discussion; "
            "re-run with --replace to start a fresh posts-only clone"
        )
    if clone_state.creation_marker is None:
        # Always a secret suffix: the bare prefix is computable from public ids,
        # so anyone could title a group with it and hand the account its
        # ownership to be adopted. Saved before creation, so a crashed init
        # still finds its own half-created peers; a --replace never re-adopts
        # the previous init's.
        nonce = secrets.token_hex(3)
        clone_state.creation_marker = f"tgcli-clone-{clone_id[:12]}-{nonce}"
    marker = clone_state.creation_marker
    state.save(clone_state)
    forum = clone_state.destination_kind == "forum"
    shape_ok = (
        topics.is_forum_destination if forum else init_peers.is_private_owned_broadcast
    )
    kind_name = "forum megagroup" if forum else "broadcast channel"
    if clone_state.destination_peer_id is not None:
        destination = await resolve.resolve_destination(
            tg, clone_state.destination_peer_id
        )
        if not shape_ok(destination):
            raise PolicyError(f"clone destination is not a private owned {kind_name}")
    else:
        valid, wrong_shape = await init_peers.marker_candidates(tg, marker, shape_ok)
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
            update = await tg(
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
        await topics.ensure_forum(lambda request: tg(request), destination)
    titled = attribution.destination_title(clone_state.source_title)
    if getattr(destination, "title", None) != titled:
        safety.append_audit("clone-init-title", account_alias, {"clone_id": clone_id})
        await tg(
            functions.channels.EditTitleRequest(channel=destination, title=titled),
        )
        destination.title = titled
    resolve.record_destination_name(clone_state, destination)
    state.save(clone_state)
    full_chat = await init_peers.copy_profile(
        tg,
        entity,
        destination,
        account_alias,
        clone_state,
    )
    if no_comments:
        clone_state.comments = "disabled"
        state.save(clone_state)
    else:
        await init_peers.init_discussion(
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
        except TRANSIENT_ERRORS:
            # CONTRACT §4: a genuine flood is exit 5 with retry_after, and a
            # 500 an error, never a quiet "muted: false" on a successful init.
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
