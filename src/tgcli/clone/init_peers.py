"""Destination peer shape, marker adoption, and profile copy for `clone init`.

`init` creates or re-adopts the destination (and, for a broadcast source with
a readable linked group, its discussion group), then copies the source's
description and avatar onto it. Those mechanics live here so the command
module keeps the commit sequence and its audit boundaries (ADR-0020/0023/0044).
"""

from __future__ import annotations

import tempfile
from pathlib import Path
from typing import Any

from telethon import errors as telethon_errors
from telethon.tl import functions, types

from tgcli import safety
from tgcli.clone import (
    attribution,
    cooldown as cooldown_mod,
    discussion,
    state,
)
from tgcli.errors import PolicyError


def is_private_owned_broadcast(entity, *, title: str | None = None) -> bool:
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


async def marker_candidates(tg, marker: str, shape_ok) -> tuple[list[Any], list[Any]]:
    valid = []
    wrong_shape = []
    async for dialog in tg.iter_dialogs():
        entity = getattr(dialog, "entity", None)
        if getattr(entity, "title", None) != marker:
            continue
        (valid if shape_ok(entity, title=marker) else wrong_shape).append(entity)
    return valid, wrong_shape


async def copy_profile(tg, source, destination, account_alias, clone_state, cooldown):
    """Copies about/avatar onto destination; returns the source's full chat.

    The avatar copy is idempotent: the copied source photo id is recorded in
    clone state, so an init re-run skips the download/upload/EditPhoto chain
    (and its service message) until the source avatar actually changes.
    """
    clone_id = clone_state.clone_id
    if isinstance(source, types.User):
        full = await cooldown(
            lambda: tg(functions.users.GetFullUserRequest(source))  # type: ignore  # Telethon resolves the entity
        )
        about = getattr(full.full_user, "about", None) or ""
    elif isinstance(source, types.Chat):
        full = await cooldown(
            lambda: tg(functions.messages.GetFullChatRequest(chat_id=source.id))
        )
        about = getattr(full.full_chat, "about", None) or ""
    else:
        full = await cooldown(
            lambda: tg(functions.channels.GetFullChannelRequest(source))
        )
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
                lambda: tg(
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
            lambda: tg.download_profile_photo(source, file=Path(workdir) / "avatar")
        )
        if downloaded is None:
            raise PolicyError("clone source avatar download failed")
        uploaded = await cooldown(lambda: tg.upload_file(downloaded))
        safety.append_audit(
            "clone-init-avatar",
            account_alias,
            {
                "clone_id": clone_id,
                "source_peer_id": source.id,
            },
        )
        await cooldown(
            lambda: tg(
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


async def init_discussion(
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

    async def cooldown(make_awaitable):
        return await make_awaitable()

    def mutate(request):
        return cooldown_mod.mutate(tg, request)

    try:
        source_group = await tg.get_entity(types.PeerChannel(linked))
        await cooldown(lambda: tg.get_messages(source_group, limit=1))
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
        lambda marker, shape_ok: marker_candidates(tg, marker, shape_ok),
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
    await copy_profile(tg, source_group, group, account_alias, clone_state, cooldown)
    safety.append_audit(
        "clone-init-discussion-link", account_alias, {"clone_id": clone_id}
    )
    await discussion.ensure_linked(mutate, destination, group)
    clone_state.discussion_linked = True
    state.save(clone_state)
