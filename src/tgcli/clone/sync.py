"""`clone sync`: copy new source posts and comments into the destination.

The run behind `tg clone sync` (ADR-0017/0021): it checks the recorded
destination, alternates the posts and comments legs (ADR-0051), carries pins
over and lists the roster, then reports the sync document.
"""

from __future__ import annotations

from datetime import UTC, datetime

from telethon.tl import types

from tgcli.clone import (
    batching,
    comments,
    discussion,
    fidelity,
    init_peers,
    legs,
    pin,
    progress as clone_progress,
    quotes,
    resolve,
    roster,
    send as clone_send,
    state,
    topics,
    transport,
)
from tgcli.errors import PartialFailure, PolicyError
from tgcli.governor import pacing
from tgcli.output import note


async def sync_text(
    tg, source: str, account_alias: str, *, limit: int | None = None
) -> dict:
    me = await tg.get_me()
    source_entity, source_kind, _ = await resolve.resolve_source(
        tg, source, account_user_id=me.id
    )
    with state.slot_lock(state.clone_id(me.id, source_entity.id, source_kind)):
        return await _sync_slot(
            tg, source, account_alias, me, source_entity, source_kind, limit=limit
        )


async def _sync_slot(
    tg, source, account_alias, me, source_entity, source_kind, *, limit
) -> dict:
    clone_state = state.load(state.resolve_slot(me.id, source_entity.id, source_kind))
    if clone_state is None or clone_state.destination_peer_id is None:
        raise PolicyError("clone is not initialized; run clone init first")
    if clone_state.source_kind != source_kind:
        raise PolicyError("clone source kind no longer matches initialized state")
    if clone_state.comments == "enabled" and not clone_state.discussion_linked:
        raise PolicyError(
            "clone discussion group is not linked yet; finish the clone with: "
            f"tg clone init {source} (then --commit the preview it prints)"
        )
    # Warn before the work, not only after it: a run killed by FloodWait never
    # reaches the tail, so the resume is where the operator sees this.
    warned_unstarted = clone_progress.comments_unstarted(clone_state)
    destination = await resolve.resolve_destination(tg, clone_state.destination_peer_id)
    resolve.record_destination_name(clone_state, destination)
    state.save(clone_state)
    forum = clone_state.destination_kind == "forum"
    valid_destination = (
        (
            topics.is_forum_destination(destination)
            and getattr(destination, "forum", False)
        )
        if forum
        else init_peers.is_private_owned_broadcast(destination)
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
    resolve_ctx = quotes.ResolveContext(tg=tg, mutate=tg, destination=destination)
    progress = clone_progress.SyncProgress(
        source_entity.id, copied=len(clone_state.id_map)
    )
    posts_exhausted = False

    async def copy_batch(messages, leg: legs.Leg, source, dest) -> None:
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
        await progress.resolve_total(tg, source)
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
                tg,
                source,
                dest,
                clone_state,
                topics.topic_id_of(messages[0]),
                counters,
                account_alias=account_alias,
            )
        batch_copied, mode, flattened, flattened_quote = await quotes.send_with_degrade(
            lambda active_plan: clone_send.forward_batch(
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
                            tg,
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
                tg,
                copy_batch,
                counters,
                lambda: limit is not None and copied_batches >= limit,
                resolve_ctx,
                posts_exhausted=posts_exhausted,
            )
            if posts_exhausted:
                break
            if clone_state.comments != "enabled":
                # The group refused mid-run and comments degraded (ADR-0061):
                # the remaining posts copy alone.
                await run_posts_window(None)
                break
    else:
        await run_posts_window(None)

    # A run the cap stopped stopped early: no pin phase and no roster listing
    # (CONTRACT §11), and `remaining` reflects the copy, not later phases.
    capped = cap_exhausted()
    # ADR-0055: pin carry-over is broadcast-only; forum sync JSON omits `pinned`.
    pinned_result = None
    if not forum:
        if not more and not capped:
            pinned_result = await pin.sync_phase(
                tg,
                clone_state,
                source_entity,
                destination,
                tg,
                account_alias,
            )
        else:
            pinned_result = pin.snapshot(clone_state)
    if not warned_unstarted:
        clone_progress.comments_unstarted(clone_state)
    progress.phase("roster")
    participants = (
        roster.deferred(clone_state, "wall_clock_cap")
        if cap_exhausted()  # the pin phase may have spent the rest of the cap
        else await roster.collect(tg, clone_state, source_entity)
    )
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
    data["remaining"] = bool(more or capped)
    if quote_flattened:
        raise PartialFailure(
            f"clone sync finished with {len(quote_flattened)} quote fallback(s)",
            data,
            cause=PolicyError("clone planted quote fallback(s)"),
            rows=sync_rows(data),
        )
    if capped and not more:
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
