"""Resolve clone reply classifications into native quotes or fallbacks (ADR-0036).

Client-bound: reachability probes, cross-leg anchor walks, and send-time
retry live here. ``replies`` stays a pure classifier; rendered degradation
lives in ``quote_fallback`` (ADR-0037).
"""

from __future__ import annotations

from copy import copy
from dataclasses import dataclass, field, replace
from typing import Any, cast

from telethon import errors as telethon_errors
from telethon.tl import functions, types

from tgcli.clone import attribution, discussion, quote_fallback, replies, transport


@dataclass
class ResolveContext:
    """Per-sync-run state shared by both clone legs."""

    tg: Any
    mutate: Any
    destination: object | None = None
    source_group: object | None = None
    source_channel_id: int | None = None
    anchors: dict = field(default_factory=dict)
    anchor_cache: dict = field(default_factory=dict)
    peer_reachable: dict = field(default_factory=dict)
    peer_entities: dict = field(default_factory=dict)
    peer_titles: dict = field(default_factory=dict)


def _quote_fields(classified: replies.Classification):
    return (
        classified.quote_text,
        list(classified.quote_entities) or None,
        classified.quote_offset,
    )


# A peer the account may not touch: the same answers Telegram gives to a probe
# and to a send that carries the peer as a reply target.
FOREIGN_SEND_ERRORS = (
    telethon_errors.ChannelPrivateError,
    telethon_errors.ChatAdminRequiredError,
    telethon_errors.ChannelInvalidError,
)


async def _probe_reachable(ctx: ResolveContext, peer) -> tuple[bool, object | None]:
    key = attribution.peer_key(peer)
    if key in ctx.peer_reachable:
        return ctx.peer_reachable[key], ctx.peer_entities.get(key)
    entity = None
    try:
        entity = await ctx.tg.get_entity(peer)
        await ctx.tg.get_messages(entity, limit=1)
        reachable = True
    except (ValueError, *FOREIGN_SEND_ERRORS):
        reachable = False
    ctx.peer_reachable[key] = reachable
    ctx.peer_entities[key] = entity
    return reachable, entity


async def _peer_title(ctx: ResolveContext, peer, message) -> str | None:
    """Title of a peer the account cannot open, read off the enclosing response.

    Telegram ships ``ChannelForbidden`` / ``ChatForbidden`` — id and title, no
    access hash — in the same history response that carried the quoting
    message. That is how official clients label a quote from a channel the
    account is banned from, and it is the only place the title is available:
    resolving the bare ``PeerChannel`` raises ``ChannelPrivateError``.
    """
    key = attribution.peer_key(peer)
    if key in ctx.peer_titles:
        return ctx.peer_titles[key]
    enclosing = getattr(message, "peer_id", None)
    if enclosing is None:
        return None
    history = None
    try:
        history = await ctx.tg(
            functions.messages.GetHistoryRequest(
                peer=enclosing,
                offset_id=message.id + 1,
                offset_date=None,
                add_offset=0,
                limit=1,
                max_id=0,
                min_id=0,
                hash=0,
            )
        )
    except (ValueError, TypeError, telethon_errors.RPCError):
        history = None
    title = None
    for chat in getattr(history, "chats", None) or ():
        # Match the plain channel id, never the -100-prefixed form: the source
        # response can carry an unrelated chat whose id merely contains it.
        if getattr(chat, "id", None) == key[1]:
            title = getattr(chat, "title", None) or None
            break
    ctx.peer_titles[key] = title
    return title


def _other_dest(leg, source_id: int) -> int | None:
    clone_state = leg.clone_state
    if leg.map_field == "discussion_id_map":
        return clone_state.dest_for(source_id)
    return clone_state.discussion_dest_for(source_id)


async def _destination_anchor(
    ctx: ResolveContext, destination_post_id: int
) -> int | None:
    if ctx.destination is None:
        return None
    return await discussion.anchor_for(
        ctx.mutate, ctx.destination, destination_post_id, ctx.anchor_cache
    )


async def _cross_leg_reply(
    classified: replies.Classification, leg, ctx: ResolveContext
) -> types.InputReplyToMessage | None:
    assert classified.parent_id is not None
    destination_post_id = _other_dest(leg, classified.parent_id)
    if destination_post_id is None:
        return None
    # Discussion leg quotes a channel post: point at the destination's
    # discussion anchor for that post (same walk as thread-root remap).
    if leg.map_field == "discussion_id_map":
        found = await _destination_anchor(ctx, destination_post_id)
        if found is None:
            return None
        reply_to_msg_id = found
    else:
        reply_to_msg_id = destination_post_id
    quote_text, quote_entities, quote_offset = _quote_fields(classified)
    top_destination_id = None
    if classified.top_id is not None:
        top_destination_id = leg.dest_for(classified.top_id)
    return types.InputReplyToMessage(
        reply_to_msg_id=reply_to_msg_id,
        top_msg_id=top_destination_id,
        quote_text=quote_text,
        quote_entities=quote_entities,
        quote_offset=quote_offset,
    )


async def _foreign_native(
    classified: replies.Classification, leg, ctx: ResolveContext
) -> types.InputReplyToMessage | None:
    assert classified.parent_id is not None and classified.peer is not None
    input_peer = await ctx.tg.get_input_entity(classified.peer)
    quote_text, quote_entities, quote_offset = _quote_fields(classified)
    top_destination_id = (
        leg.dest_for(classified.top_id) if classified.top_id is not None else None
    )
    return types.InputReplyToMessage(
        reply_to_msg_id=classified.parent_id,
        reply_to_peer_id=input_peer,
        top_msg_id=top_destination_id,
        quote_text=quote_text,
        quote_entities=quote_entities,
        quote_offset=quote_offset,
    )


async def _source_post(ctx: ResolveContext, anchor_id: int):
    if ctx.source_group is None or ctx.source_channel_id is None:
        return None
    if anchor_id not in ctx.anchors:
        found = await ctx.tg.get_messages(ctx.source_group, ids=anchor_id)
        ctx.anchors[anchor_id] = (
            None
            if found is None
            else discussion.autoforward_post_id(found, ctx.source_channel_id)
        )
    return ctx.anchors[anchor_id]


async def _place_thread(
    messages, plan: transport.TransportPlan, leg, ctx: ResolveContext
) -> transport.TransportPlan:
    """Re-point a comment's thread root at the destination's own anchor.

    Folded from ``comments._remap`` so both legs share one resolution walk.
    """
    if ctx.source_group is None or ctx.source_channel_id is None:
        return plan
    header = getattr(messages[0], "reply_to", None)
    if not isinstance(header, types.MessageReplyHeader):
        return plan
    top = header.reply_to_top_id
    # ``reply_to_top_id`` always names this group; ``reply_to_msg_id`` only when
    # the header carries no other peer. Walking a foreign parent id here is
    # ADR-0036 §4's silent failure — the group has its own message under that
    # id, and matching an anchor re-points the reply at an unrelated post.
    peer = header.reply_to_peer_id
    root = top
    if root is None and (peer is None or attribution.same_peer(peer, ctx.source_group)):
        root = header.reply_to_msg_id
    if root is None:
        return plan
    post_id = await _source_post(ctx, root)
    destination_post_id = None if post_id is None else leg.clone_state.dest_for(post_id)
    if destination_post_id is None:
        # Fallback / foreign with a mapped discussion top: place under it.
        if (
            plan.body_prefix is not None
            and top is not None
            and (mapped_top := leg.dest_for(top)) is not None
            and plan.reply_to is None
        ):
            return transport.as_reuploaded(
                replace(
                    plan,
                    reply_to=types.InputReplyToMessage(reply_to_msg_id=mapped_top),
                )
            )
        return plan
    found = await _destination_anchor(ctx, destination_post_id)
    if found is None:
        return plan
    if top is None:
        reply_to = types.InputReplyToMessage(
            reply_to_msg_id=found,
            quote_text=header.quote_text,
            quote_entities=list(header.quote_entities or ()) or None,
            quote_offset=header.quote_offset,
        )
    elif plan.reply_to is None:
        if plan.body_prefix is not None:
            reply_to = types.InputReplyToMessage(reply_to_msg_id=found)
        else:
            return plan
    else:
        reply_to = cast(types.InputReplyToMessage, copy(plan.reply_to))
        reply_to.top_msg_id = found
    return transport.as_reuploaded(replace(plan, reply_to=reply_to))


async def _unreachable_fallback(
    messages,
    plan: transport.TransportPlan,
    classified: replies.Classification,
    ctx: ResolveContext,
    entity=None,
) -> transport.TransportPlan:
    # A resolved entity already carries a display name; only the peers we could
    # not open need the title read off the enclosing history response.
    title = (
        None
        if entity is not None
        else await _peer_title(ctx, classified.peer, messages[0])
    )
    return quote_fallback.fallback_plan(
        messages,
        plan,
        classified,
        reason="unreachable",
        entity=entity,
        peer_title=title,
    )


async def resolve(
    messages, plan: transport.TransportPlan, leg, source, ctx: ResolveContext
) -> transport.TransportPlan:
    """Turn ``transport.decide``'s plan into a sendable reply or fallback."""
    classified = replies.target(messages, leg, source)
    if classified is None:
        return plan

    if classified.kind == "mapped-in-leg":
        reply_to = replies.input_reply(classified, leg)
        if reply_to is not None:
            plan = replace(plan, reply_to=reply_to, reply_flattened=False)
    elif classified.kind == "mapped-cross-leg":
        reply_to = await _cross_leg_reply(classified, leg, ctx)
        if reply_to is not None:
            plan = transport.as_reuploaded(replace(plan, reply_to=reply_to))
    elif classified.kind == "foreign-peer":
        reachable, entity = await _probe_reachable(ctx, classified.peer)
        if reachable:
            try:
                reply_to = await _foreign_native(classified, leg, ctx)
            except ValueError:
                reply_to = None
            if reply_to is not None:
                plan = transport.as_reuploaded(replace(plan, reply_to=reply_to))
            else:
                plan = await _unreachable_fallback(
                    messages, plan, classified, ctx, entity=entity
                )
        else:
            plan = await _unreachable_fallback(messages, plan, classified, ctx)

    return await _place_thread(messages, plan, leg, ctx)


def degrade_to_fallback(
    messages, plan: transport.TransportPlan, leg, source, ctx: ResolveContext
) -> transport.TransportPlan:
    """A reachable foreign send that Telegram rejected → rendered fallback."""
    classified = replies.target(messages, leg, source)
    if classified is None or classified.kind != "foreign-peer":
        return plan
    key = attribution.peer_key(classified.peer)
    ctx.peer_reachable[key] = False
    entity = ctx.peer_entities.get(key)
    return quote_fallback.fallback_plan(
        messages,
        plan,
        classified,
        reason="rejected",
        entity=entity,
        placement=_degraded_placement(plan, classified, leg),
    )


def _degraded_placement(plan, classified: replies.Classification, leg) -> int | None:
    """Keep the thread the rejected send had already resolved (ADR-0036 §2).

    ``_place_thread`` walks source anchor -> post -> destination anchor, and a
    discussion top is itself an auto-forward anchor that never enters the leg's
    map — so ``dest_for`` alone cannot recover the placement it found.
    """
    resolved_top = getattr(plan.reply_to, "top_msg_id", None)
    if resolved_top is not None:
        return resolved_top
    if classified.top_id is not None:
        return leg.dest_for(classified.top_id)
    return None


def foreign_quote_reply(plan) -> bool:
    reply_to = plan.reply_to
    return (
        isinstance(reply_to, types.InputReplyToMessage)
        and reply_to.reply_to_peer_id is not None
    )


async def send_with_degrade(send, messages, plan, leg, source, ctx):
    """Retry once with fallback when a native foreign quote is rejected."""
    try:
        return await send(plan)
    except FOREIGN_SEND_ERRORS:
        if not foreign_quote_reply(plan):
            raise
        degraded = degrade_to_fallback(messages, plan, leg, source, ctx)
        return await send(degraded)
    except telethon_errors.BadRequestError as error:
        stripped = quote_fallback.drop_stale_quote(messages, plan, error)
        if stripped is None:
            raise
        return await send(stripped)
