"""Phase 2 of clone sync: copy a source discussion group into the clone's own.

Anchors are Telegram's auto-forwards of channel posts into the linked group.
They already exist on both sides, so they are never copied — they are only read,
as the source-anchor -> source-post map that lets a comment find the destination
thread it belongs to.
"""

from typing import cast

from telethon import errors as telethon_errors
from telethon.tl import types

from tgcli.clone import batching, discussion, legs, replies, state
from tgcli.errors import PolicyError


def _anchor_posts(messages, source_channel_id) -> dict[int, int] | None:
    """Anchor id -> source post id for a batch of Telegram's own auto-forwards,
    or None when the batch is real content. A channel album auto-forwards as an
    album, so an anchor batch can carry several messages."""
    found = {
        message.id: discussion.autoforward_post_id(message, source_channel_id)
        for message in messages
    }
    if all(post_id is None for post_id in found.values()):
        return None
    if any(post_id is None for post_id in found.values()):
        raise PolicyError("clone discussion anchor album is incomplete")
    return cast(dict[int, int], found)


async def sync_phase(
    tg,
    clone_state,
    source_channel,
    destination,
    mutate,
    copy_batch,
    counters,
    limited,
    resolve_ctx,
    *,
    posts_exhausted: bool = False,
) -> bool:
    """Copy the source discussion group into the clone's. Parent posts that
    this phase needs must already be mapped, or a cross-leg comment whose
    parent sits beyond the posts cursor defers (ADR-0051) instead of
    flattening — unless the posts leg is already exhausted, in which case
    that parent is permanently gone and flattens. Returns True when the
    --limit stop landed inside this phase."""
    leg = legs.discussion(clone_state)

    def degrade() -> None:
        # Same refusal shape as attribution._resolve / roster.collect
        # (1.2.8): a linked group that turned private after init must not
        # crash a sync whose posts have already copied. Degrade to the
        # ADR-0023 honest marker; never join the source on the user's
        # behalf. Clearing discussion_cursor / discussion_id_map is
        # required: state.from_dict rejects comments != enabled with
        # leftover phase-2 progress.
        clone_state.clear_discussion_progress()
        clone_state.comments = "unavailable"
        state.save(clone_state)

    # ADR-0061: one shared ResolveContext spans every window of a sync run
    # (ADR-0051 interleave), and the discussion peers cannot change identity
    # mid-run — later windows reuse the first window's entities instead of
    # re-paying two GetChannels RPCs. Because that skips the per-window
    # re-resolve, a group that turns private between windows now surfaces on
    # the window's own reads — the iterator below carries the same degrade
    # guard (ADR-0061 review fix).
    source_group = getattr(resolve_ctx, "source_group", None)
    if source_group is None:
        try:
            source_group = await tg.get_entity(
                types.PeerChannel(clone_state.discussion_source_peer_id)
            )
        except telethon_errors.FloodWaitError:
            # Still arm ADR-0045 via the caller's cooldown wrapper — never
            # swallow.
            raise
        except (ValueError, telethon_errors.RPCError):
            degrade()
            return False
    group = getattr(resolve_ctx, "destination_group", None)
    if group is None:
        try:
            group = await tg.get_entity(
                types.PeerChannel(clone_state.discussion_destination_peer_id)
            )
        except discussion.PEER_UNAVAILABLE:
            raise PolicyError("clone discussion destination is unavailable") from None
        if not discussion.is_discussion_destination(group):
            raise PolicyError(
                "clone discussion destination is not a private owned megagroup"
            )
        resolve_ctx.destination_group = group
    await discussion.verify_tail(
        tg,
        group,
        clone_state.max_discussion_destination_id(),
        "discussion destination",
        lambda item: (
            getattr(item, "action", None) is not None
            or discussion.autoforward_post_id(item, destination.id) is not None
        ),
    )
    resolve_ctx.source_group = source_group
    resolve_ctx.source_channel_id = source_channel.id
    resolve_ctx.destination = destination

    events = aiter(
        batching.plan(tg.iter_messages(source_group, min_id=leg.cursor, reverse=True))
    )
    while True:
        try:
            event = await anext(events)
        except StopAsyncIteration:
            break
        except telethon_errors.FloodWaitError:
            # Still arm ADR-0045 via the caller's cooldown wrapper.
            raise
        except (ValueError, telethon_errors.RPCError):
            # The window's own reads are where a mid-run privatized source
            # surfaces once entities are cached (ADR-0061 review fix). Only
            # the source iterator gets the degrade guard: copy_batch failures
            # below keep escaping exactly as before.
            degrade()
            return False
        if limited():
            return True
        if isinstance(event, batching.ServiceSkip):
            counters["skipped_service"] += 1
            leg.cursor = event.message_id
        elif (posts := _anchor_posts(event.messages, source_channel.id)) is not None:
            # ADR-0051: stop before an anchor whose source post is newer than
            # the posts cursor — but only while more posts may still arrive.
            # Once the posts leg is exhausted, beyond-cursor anchors are
            # orphans (deleted/never-seen) and must not stall the leg.
            if not posts_exhausted and any(
                post_id > clone_state.cursor for post_id in posts.values()
            ):
                return False
            resolve_ctx.anchors.update(posts)
            counters["skipped_autoforward"] += len(posts)
            leg.cursor = event.messages[-1].id
        else:
            classified = replies.target(
                event.messages,
                leg,
                source_group,
                posts_cursor=clone_state.cursor,
                posts_exhausted=posts_exhausted,
            )
            if classified is not None and classified.kind == "deferred":
                return False
            await copy_batch(event.messages, leg, source_group, group)
            continue
        state.save(clone_state)
    return False
