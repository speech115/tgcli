"""Phase 2 of clone sync: copy a source discussion group into the clone's own.

Anchors are Telegram's auto-forwards of channel posts into the linked group.
They already exist on both sides, so they are never copied — they are only read,
as the source-anchor -> source-post map that lets a comment find the destination
thread it belongs to.
"""

from typing import cast

from telethon import errors as telethon_errors
from telethon.tl import types

from tgcli import pace
from tgcli.clone import batching, discussion, journal, legs, replies, state
from tgcli.clone.ids import SourceCommentId, SourcePostId
from tgcli.clone.state import Comments
from tgcli.errors import PolicyError
from tgcli.tg_errors import NOT_REFUSALS, unless_refused


def _anchor_posts(
    messages, source_channel_id
) -> dict[SourceCommentId, SourcePostId] | None:
    """Anchor id -> source post id for a batch of Telegram's own auto-forwards,
    or None when the batch is real content. A channel album auto-forwards as an
    album, so an anchor batch can carry several messages."""
    found = {
        SourceCommentId(message.id): discussion.autoforward_post_id(
            message, source_channel_id
        )
        for message in messages
    }
    if all(post_id is None for post_id in found.values()):
        return None
    if any(post_id is None for post_id in found.values()):
        raise PolicyError("clone discussion anchor album is incomplete")
    return cast(dict[SourceCommentId, SourcePostId], found)


async def sync_phase(
    tg,
    clone_state,
    source_channel,
    destination,
    copy_batch,
    counters,
    limited,
    resolve_ctx,
    *,
    posts_exhausted: bool = False,
) -> bool:
    """Copy the source discussion group into the clone's. Parent posts that
    this phase needs must already be mapped, or a cross-leg comment whose
    parent sits beyond the posts cursor defers instead of
    flattening — unless the posts leg is already exhausted, in which case
    that parent is permanently gone and flattens. Returns True when the
    --limit stop landed inside this phase."""
    leg = legs.discussion(clone_state)

    def degrade() -> None:
        clone_state.clear_discussion_progress()
        clone_state.comments = Comments.UNAVAILABLE
        state.save(clone_state)

    source_group = getattr(resolve_ctx, "source_group", None)
    if source_group is None:
        source_group, refusal = await unless_refused(
            tg.get_entity(types.PeerChannel(clone_state.discussion_source_peer_id))
        )
        if refusal is not None:
            degrade()
            return False
    group = getattr(resolve_ctx, "destination_group", None)
    if group is None:
        group = await discussion.destination_group(
            tg, clone_state.discussion_destination_peer_id
        )
        resolve_ctx.destination_group = group
    await journal.guard_tail(
        tg,
        group,
        leg,
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
        batching.plan(
            tg.iter_messages(
                source_group,
                min_id=leg.cursor,
                reverse=True,
                wait_time=pace.PAGE_WAIT_S,
            )
        )
    )
    while True:
        try:
            event = await anext(events)
        except StopAsyncIteration:
            break
        except NOT_REFUSALS:
            raise
        except (ValueError, telethon_errors.RPCError):
            degrade()
            return False
        if limited():
            return True
        if pace.exhausted():
            return False
        if isinstance(event, batching.ServiceSkip):
            counters["skipped_service"] += 1
            leg.cursor = event.message_id
        elif (posts := _anchor_posts(event.messages, source_channel.id)) is not None:
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
