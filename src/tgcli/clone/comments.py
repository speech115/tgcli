"""Phase 2 of clone sync: copy a source discussion group into the clone's own.

Anchors are Telegram's auto-forwards of channel posts into the linked group.
They already exist on both sides, so they are never copied — they are only read,
as the source-anchor -> source-post map that lets a comment find the destination
thread it belongs to.
"""
from dataclasses import replace
from telethon.tl import types
from tgcli.clone import batching, discussion, legs, state
from tgcli.errors import PolicyError


async def _source_post(tg, source_group, source_channel_id, anchor_id, anchors):
    """The source post an anchor belongs to, else None for a plain message.
    Anchors met while walking phase 2 are already cached; earlier ones (and
    non-anchors, negatively) are looked up once."""
    if anchor_id not in anchors:
        found = await tg.get_messages(source_group, ids=anchor_id)
        anchors[anchor_id] = (
            None if found is None
            else discussion.autoforward_post_id(found, source_channel_id))
    return anchors[anchor_id]


async def _remap(tg, mutate, clone_state, source_group, source_channel_id,
                 destination, messages, plan, anchors, cache):
    """Re-point a comment's thread root at the destination's own anchor:
    source anchor -> source post -> destination post -> destination anchor.
    Anything unmappable is left alone and flattens on the existing rules."""
    header = getattr(messages[0], "reply_to", None)
    if not isinstance(header, types.MessageReplyHeader):
        return plan
    top = header.reply_to_top_id
    root = top if top is not None else header.reply_to_msg_id
    post_id = await _source_post(
        tg, source_group, source_channel_id, root, anchors)
    destination_post_id = None if post_id is None else clone_state.dest_for(post_id)
    if destination_post_id is None:
        return plan
    found = await discussion.anchor_for(
        mutate, destination, destination_post_id, cache)
    if found is None:
        return plan
    if top is None:
        reply_to = types.InputReplyToMessage(
            reply_to_msg_id=found, quote_text=header.quote_text,
            quote_entities=list(header.quote_entities or ()) or None,
            quote_offset=header.quote_offset)
    elif plan.reply_to is None:
        return plan
    else:
        reply_to = plan.reply_to
        reply_to.top_msg_id = found
    return replace(plan, reply_to=reply_to, reply_flattened=False,
                   needs_author=True,
                   mode="snapshots" if plan.mode == "snapshots" else "reuploaded")


def _anchor_posts(messages, source_channel_id) -> dict[int, int] | None:
    """Anchor id -> source post id for a batch of Telegram's own auto-forwards,
    or None when the batch is real content. A channel album auto-forwards as an
    album, so an anchor batch can carry several messages."""
    found = {message.id: discussion.autoforward_post_id(message, source_channel_id)
             for message in messages}
    if all(post_id is None for post_id in found.values()):
        return None
    if any(post_id is None for post_id in found.values()):
        raise PolicyError("clone discussion anchor album is incomplete")
    return found


async def sync_phase(tg, clone_state, source_channel, destination, mutate,
                     copy_batch, counters, limited) -> bool:
    """Copy the source discussion group into the clone's. Phase 1 has already
    run to exhaustion, so every parent post is mapped. Returns True when the
    --limit stop landed inside this phase."""
    leg = legs.discussion(clone_state)
    source_group = await tg.get_entity(
        types.PeerChannel(clone_state.discussion_source_peer_id))
    try:
        group = await tg.get_entity(
            types.PeerChannel(clone_state.discussion_destination_peer_id))
    except ValueError:
        raise PolicyError("clone discussion destination is unavailable") from None
    if not discussion.is_discussion_destination(group):
        raise PolicyError(
            "clone discussion destination is not a private owned megagroup")
    await discussion.verify_tail(
        tg, group, clone_state.max_discussion_destination_id(),
        "discussion destination",
        lambda item: getattr(item, "action", None) is not None
        or discussion.autoforward_post_id(item, destination.id) is not None)
    anchors, cache = {}, {}

    async def remap(messages, plan):
        return await _remap(tg, mutate, clone_state, source_group,
                            source_channel.id, destination, messages, plan,
                            anchors, cache)

    async for event in batching.plan(tg.iter_messages(
            source_group, min_id=leg.cursor, reverse=True)):
        if limited():
            return True
        if isinstance(event, batching.ServiceSkip):
            counters["skipped_service"] += 1
            leg.cursor = event.message_id
        elif (posts := _anchor_posts(event.messages, source_channel.id)) is not None:
            anchors.update(posts)
            counters["skipped_autoforward"] += len(posts)
            leg.cursor = event.messages[-1].id
        else:
            await copy_batch(event.messages, leg, source_group, group, remap)
            continue
        state.save(clone_state)
    return False
