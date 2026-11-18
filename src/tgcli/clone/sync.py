"""`clone sync`: copy new source posts and comments into the destination.

The run behind `tg clone sync` (ADR-0017/0021): it checks the recorded
destination, alternates the posts and comments legs (ADR-0051), carries pins
over and lists the roster, then reports the sync document. A run with nothing
new finishes early (ADR-0126, `clone.idle`), and one that ends in a FloodWait
it cannot ride out remembers it in the clone, so the next run asks nothing of
Telegram until it has passed.

Invariants (tests: test_clone_sync_faults' CloneSyncFaultMachine):

- L1. A batch's cursor and map rows land in one SQLite transaction after
  Telegram confirmed every message; the cursor never passes an unmapped copy.
- L2. A copy Telegram made but tgcli did not record (a crash between send and
  save) is caught before the next copy. Each send first saves its leg's send
  intent; ``journal.guard_tail`` maps a tail that is exactly the copies the
  intent names, and refuses any other destination message above the highest
  mapped id (exit 2, manual repair). A split caption's rest is recorded, so it
  counts as mapped.
- L3. The reupload cache is cleared only after the batch's mapping is saved,
  so a crash resumes the download.
"""

from __future__ import annotations

from datetime import UTC, datetime

from telethon.tl import types

from tgcli import pace
from tgcli.clone import (
    batching,
    comments,
    fidelity,
    idle,
    init_peers,
    journal,
    legs,
    lookup,
    pin,
    progress as clone_progress,
    quotes,
    replies,
    resolve,
    roster,
    send as clone_send,
    state,
    topics,
    transport,
)
from tgcli.clone.state import Comments
from tgcli.errors import PartialFailure, PolicyError, RateLimitError, TgcliError
from tgcli.output import note
from tgcli.tg_errors import FLOOD_ERRORS


async def sync_text(
    tg,
    source: str,
    account_alias: str,
    *,
    limit: int | None = None,
    known: dict[int, int] | None = None,
) -> dict:
    me_id = await idle.account_id(tg)
    peer = await idle.source_peer(tg, source, me_id)
    resolved = None
    if peer is None:
        resolved = await resolve.resolve_source(tg, source, account_user_id=me_id)
        slot = state.clone_id(me_id, resolved[0].id, resolved[1])
    else:
        slot = idle.slot_id(peer, me_id)
    with state.slot_lock(slot):
        try:
            if peer is not None and (
                done := await _finish_if_idle(tg, slot, peer, known)
            ):
                return done
            if resolved is None:
                resolved = await resolve.resolve_source(
                    tg, source, account_user_id=me_id
                )
            me = await tg.get_me()
            source_entity, source_kind, _ = resolved
            return await _sync_slot(
                tg, source, account_alias, me, source_entity, source_kind, limit=limit
            )
        except FLOOD_ERRORS as flood:
            _remember_flood(slot, flood.seconds)
            raise


async def _finish_if_idle(tg, slot: str, peer, known=None) -> dict | None:
    held = state.load(slot)
    if held is None:
        return None
    _refuse_during_flood(held)
    if not await idle.up_to_date(tg, held, peer, known):
        return None
    idle.finish(held)
    return _idle_document(held)


async def sync_all(tg, account_alias: str, *, limit: int | None = None) -> dict:
    """`clone sync --all`: every clone of this account, one after another, in
    this one session.

    A FloodWait is the account's, not one clone's: while any clone of the
    account remembers one, the run asks Telegram nothing, and a flood mid-run
    stops it there, the clones after it left for the next run. A clone that
    refuses on its own (a tail needing repair, a destination gone) is reported
    and the run goes on to the next one. Newest ids for the idle check come
    from one GetPeerDialogs per 100 peers instead of a history read per peer.
    """
    me_id = await idle.account_id(tg)
    clones = sorted(
        (found for found in lookup.loaded_states() if found.account_user_id == me_id),
        key=lambda found: found.clone_id,
    )
    waits = [left for found in clones if (left := found.flood_left()) is not None]
    if waits:
        raise RateLimitError(
            f"a clone of this account is rate limited for {max(waits)}s; "
            "no request was sent",
            retry_after=max(waits),
        )
    peers = []
    for found in clones:
        if (
            peer := await idle.source_peer(tg, lookup.source_ref(found), me_id)
        ) is not None:
            peers.append(peer)
        if (group := await idle.discussion_peer(tg, found)) is not None:
            peers.append(group)
    known = await idle.newest_ids(tg, peers)
    results: list[dict] = []
    failure: TgcliError | None = None
    for index, found in enumerate(clones):
        source = lookup.source_ref(found)
        entry: dict = {
            "id": found.clone_id,
            "source": source,
            "title": found.source_title,
        }
        try:
            entry.update(
                await sync_text(tg, source, account_alias, limit=limit, known=known)
            )
        except (*FLOOD_ERRORS, RateLimitError) as flood:
            seconds = int(
                getattr(flood, "seconds", None)
                or getattr(flood, "details", {}).get("retry_after")
                or 0
            )
            entry["error"] = {"code": "RATE_LIMIT", "retry_after": seconds}
            results.append(entry)
            data = {
                "clones": results,
                "deferred": [lookup.source_ref(rest) for rest in clones[index + 1 :]],
            }
            raise PartialFailure(
                f"clone sync --all stopped at {source}: rate limited for {seconds}s",
                data,
                cause=RateLimitError(
                    f"clone {source} is rate limited for {seconds}s",
                    retry_after=seconds,
                ),
            ) from flood
        except TgcliError as refusal:
            entry["error"] = {"code": refusal.code, "message": str(refusal)}
            failure = failure or refusal
        results.append(entry)
    data = {"clones": results, "deferred": []}
    if failure is not None:
        raise PartialFailure(
            "clone sync --all: a clone refused; see its error", data, cause=failure
        )
    return data


def sync_all_rows(data: dict) -> list[tuple]:
    rows = []
    for entry in data["clones"]:
        sync = entry.get("sync") or {}
        rows.append(
            (
                entry["source"],
                entry["title"],
                sync.get("copied"),
                sync.get("more"),
                (entry.get("error") or {}).get("code"),
            )
        )
    return rows


def _refuse_during_flood(clone_state: state.CloneState) -> None:
    left = clone_state.flood_left()
    if left is not None:
        raise RateLimitError(
            f"clone is rate limited for {left}s; no request was sent", retry_after=left
        )


def _remember_flood(slot: str, seconds: int) -> None:
    """Record a flood this run could not ride out. The state is read afresh:
    what the run held in memory but never saved stays unsaved, as before."""
    clone_state = state.load(slot)
    if clone_state is not None:
        clone_state.remember_flood(seconds)
        state.save(clone_state)


async def _sync_slot(
    tg, source, account_alias, me, source_entity, source_kind, *, limit
) -> dict:
    """One clone's run, in phases: check the recorded destination, copy the
    posts and comments legs, then carry pins over, list the roster and
    report."""
    clone_state, destination, warned_unstarted = await _open_slot(
        tg, source, me, source_entity, source_kind
    )
    run = _Run(tg, clone_state, destination, source_entity, account_alias, me, limit)
    await run.copy_legs()
    return await _finish(run, warned_unstarted)


async def _open_slot(tg, source, me, source_entity, source_kind):
    """Load the clone and check its destination before any copy: refused
    during a remembered flood, on a changed source or destination kind, an
    unlinked discussion group, or an unexpected destination tail."""
    clone_state = state.load(state.resolve_slot(me.id, source_entity.id, source_kind))
    if clone_state is None or clone_state.destination_peer_id is None:
        raise PolicyError("clone is not initialized; run clone init first")
    _refuse_during_flood(clone_state)
    if clone_state.source_kind != source_kind:
        raise PolicyError("clone source kind no longer matches initialized state")
    if clone_state.comments == Comments.ENABLED and not clone_state.discussion_linked:
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
    await journal.guard_tail(
        tg,
        destination,
        legs.posts(clone_state),
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
    return clone_state, destination, warned_unstarted


class _Run:
    """The copy phase of one clone's run and what it counts for the report."""

    def __init__(
        self, tg, clone_state, destination, source_entity, account_alias, me, limit
    ):
        self.tg = tg
        self.clone_state = clone_state
        self.destination = destination
        self.source_entity = source_entity
        self.account_alias = account_alias
        self.me = me
        self.limit = limit
        self.forum = clone_state.destination_kind == "forum"
        self.copied = 0
        self.copied_batches = 0
        self.skipped_unsupported: list[dict] = []
        self.transport_counts = {"forwarded": 0, "reuploaded": 0, "snapshots": 0}
        self.counters = {
            "topics_created": 0,
            "skipped_service": 0,
            "skipped_autoforward": 0,
        }
        self.reply_flattened = 0
        self.quote_flattened: list[dict] = []
        self.markup_dropped: list[dict] = []
        self.poll_votes: list[dict] = []
        self.author_cache: dict = {}
        self.reforward_cache: dict = {}
        self.paces = clone_send.RunPace()
        self.more = False
        self.posts_leg = legs.posts(clone_state)
        self.resolve_ctx = quotes.ResolveContext(
            tg=tg, mutate=tg, destination=destination
        )
        self.progress = clone_progress.SyncProgress(
            source_entity.id, copied=len(clone_state.id_map)
        )
        self.posts_exhausted = False
        # A post replying to a comment not copied yet waits for
        # the comments leg (`posts_waiting`), unless that leg has stalled and
        # will not reach it this run. A stall releases only the batch that
        # waited (`release_waiting`): the posts after it may reply to comments
        # that copying it unblocks.
        self.posts_waiting = False
        self.comments_stalled = clone_state.comments != Comments.ENABLED
        self.release_waiting = False

    def limit_reached(self) -> bool:
        return self.limit is not None and self.copied_batches >= self.limit

    async def copy_legs(self) -> None:
        clone_state = self.clone_state
        if clone_state.comments != Comments.ENABLED:
            await self.run_posts_window(None)
            return
        # ADR-0051: alternate posts×WINDOW with comments until both exhaust.
        # Enter comments only while --limit budget remains; a limit hit during
        # posts ends the run (same as today for limit < WINDOW).
        while not self.more and not pace.exhausted():
            # Each interleaved window re-announces its leg so the counters
            # belong to the leg that is running (ADR-0051 interleave, #174).
            self.progress.phase("posts", copied=len(clone_state.id_map))
            self.posts_waiting = False
            ran = await self.run_posts_window(legs.WINDOW)
            if self.more or pace.exhausted():
                break
            self.posts_exhausted = ran < legs.WINDOW and not self.posts_waiting
            discussion_before = clone_state.discussion_cursor
            self.progress.phase("comments", copied=len(clone_state.discussion_id_map))
            self.more = await comments.sync_phase(
                self.tg,
                clone_state,
                self.source_entity,
                self.destination,
                self.copy_batch,
                self.counters,
                self.limit_reached,
                self.resolve_ctx,
                posts_exhausted=self.posts_exhausted,
            )
            if self.posts_exhausted:
                break
            if clone_state.comments != Comments.ENABLED:
                # The group refused mid-run and comments degraded (ADR-0061):
                # the remaining posts copy alone.
                self.comments_stalled = True
                await self.run_posts_window(None)
                break
            # A waiting post needs the comments leg to move. If this window
            # did not move it, no later one will: the post goes out flat.
            self.release_waiting = (
                self.posts_waiting
                and clone_state.discussion_cursor == discussion_before
            )

    async def run_posts_window(self, max_batches: int | None) -> int:
        """Copy up to max_batches posts (None = exhaust). Sets more on --limit."""
        tg = self.tg
        ran = 0
        async for event in batching.plan(
            tg.iter_messages(
                self.source_entity,
                min_id=self.posts_leg.cursor,
                reverse=True,
                wait_time=pace.PAGE_WAIT_S,
            )
        ):
            if self.limit_reached():
                self.more = True
                break
            if pace.exhausted():
                break
            if isinstance(event, batching.ServiceSkip):
                await self._skip_service(event)
                continue
            if self.release_waiting:
                self.release_waiting = False
            elif not self.comments_stalled:
                classified = replies.target(
                    event.messages,
                    self.posts_leg,
                    self.source_entity,
                    comments_stalled=False,
                )
                if classified is not None and classified.kind == "deferred":
                    self.posts_waiting = True
                    break
            await self.copy_batch(
                event.messages, self.posts_leg, self.source_entity, self.destination
            )
            ran += 1
            if max_batches is not None and ran >= max_batches:
                break
        return ran

    async def _skip_service(self, event) -> None:
        clone_state = self.clone_state
        source_message = event.message
        if self.forum and isinstance(
            source_message.action, types.MessageActionTopicCreate
        ):
            if clone_state.topic_dest_for(source_message.id) is None:
                await topics.create_topic(
                    self.tg,
                    self.destination,
                    clone_state,
                    source_message.id,
                    account_alias=self.account_alias,
                    title=source_message.action.title,
                    icon_color=getattr(source_message.action, "icon_color", None),
                    icon_emoji_id=getattr(source_message.action, "icon_emoji_id", None),
                )
                self.counters["topics_created"] += 1
        else:
            self.counters["skipped_service"] += 1
        self.posts_leg.cursor = event.message_id
        state.save(clone_state)

    async def copy_batch(self, messages, leg: legs.AnyLeg, source, dest) -> None:
        clone_state = self.clone_state
        tg = self.tg
        unsupported = [
            {"id": message.id, "kind": kind}
            for message in messages
            if (kind := fidelity.unsupported_kind(message)) is not None
        ]
        if unsupported:
            self.skipped_unsupported.extend(unsupported)
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
        await self.progress.resolve_total(tg, source)
        plan = transport.decide(
            messages,
            leg,
            source,
            posts_cursor=clone_state.cursor,
            posts_exhausted=self.posts_exhausted,
        )
        if plan.mode == "deferred":
            return
        plan = await quotes.resolve(
            messages,
            plan,
            leg,
            source,
            self.resolve_ctx,
            posts_cursor=clone_state.cursor,
            posts_exhausted=self.posts_exhausted,
        )
        topic_dest = None
        if self.forum:
            topic_dest = await topics.ensure_topic(
                tg,
                source,
                dest,
                clone_state,
                topics.topic_id_of(messages[0]),
                self.counters,
                account_alias=self.account_alias,
            )
        batch_copied, mode, flattened, flattened_quote = await quotes.send_with_degrade(
            lambda active_plan: clone_send.forward_batch(
                tg,
                source,
                dest,
                clone_state,
                leg,
                self.account_alias,
                list(messages),
                self.me,
                self.author_cache,
                active_plan,
                topic_dest=topic_dest,
                poll_votes=self.poll_votes,
                progress=self.progress,
                reforward_cache=self.reforward_cache,
                paces=self.paces,
            ),
            list(messages),
            plan,
            leg,
            source,
            self.resolve_ctx,
        )
        self.copied += batch_copied
        self.transport_counts[mode] += batch_copied
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
                self.markup_dropped.append({"id": message.id, "buttons": buttons})
        self.reply_flattened += int(flattened)
        if flattened_quote is not None:
            self.quote_flattened.append(flattened_quote)
            note(
                f"warning: source message {flattened_quote['id']} planted quote "
                f"fallback ({flattened_quote['reason']})"
            )
        self.copied_batches += 1
        self.progress.batch(batch_copied, mode)


async def _finish(run: _Run, warned_unstarted) -> dict:
    """Pins, roster and the sync document, after the copy phase."""
    clone_state = run.clone_state
    tg = run.tg
    destination = run.destination
    more = run.more
    # A run the cap stopped stopped early: no pin phase and no roster listing
    # (the CLI contract), and `remaining` reflects the copy, not later phases.
    capped = pace.exhausted()
    # ADR-0055: pin carry-over is broadcast-only; forum sync JSON omits `pinned`.
    pinned_result = None
    if not run.forum:
        if not more and not capped:
            pinned_result = await pin.sync_phase(
                tg,
                clone_state,
                run.source_entity,
                destination,
                tg,
                run.account_alias,
            )
        else:
            pinned_result = pin.snapshot(clone_state)
    if not warned_unstarted:
        clone_progress.comments_unstarted(clone_state)
    # The pin phase may have spent the rest of the cap.
    if pace.exhausted():
        participants = roster.deferred(clone_state, "wall_clock_cap")
    elif not roster.due(clone_state):
        participants = roster.fresh(clone_state)
    else:
        run.progress.phase("roster")
        participants = await roster.collect(tg, clone_state, run.source_entity)
    clone_state.last_synced_at = datetime.now(UTC).isoformat()
    clone_state.retry_not_before = None
    state.save(clone_state)
    data = _document(
        clone_state,
        {"id": destination.id, "title": destination.title},
        {
            "copied": run.copied,
            "skipped_unsupported": run.skipped_unsupported,
            **run.transport_counts,
            **run.counters,
            "reply_flattened": run.reply_flattened,
            "quote_flattened": run.quote_flattened,
            "markup_dropped": run.markup_dropped,
            "poll_votes": run.poll_votes,
        },
        more=more,
        participants=participants,
        pinned=pinned_result,
    )
    data["remaining"] = bool(more or capped)
    if run.quote_flattened:
        raise PartialFailure(
            f"clone sync finished with {len(run.quote_flattened)} quote fallback(s)",
            data,
            cause=PolicyError("clone planted quote fallback(s)"),
            rows=sync_rows(data),
        )
    if capped and not more:
        # --max-runtime exhausted: a normal stop, not an error — the cursor
        # advanced and the next invocation resumes (ADR-0107).
        data["stop_reason"] = "wall_clock_cap"  # type: ignore[index]
        data["resume"] = {"cursor": clone_state.cursor}
    return data


def _document(
    clone_state: state.CloneState,
    destination: dict,
    copy: dict,
    *,
    more: bool,
    participants: dict,
    pinned: dict | None,
    up_to_date: bool = False,
) -> dict:
    return {
        "clone": {
            "id": clone_state.clone_id,
            "source": {
                "id": clone_state.source_peer_id,
                "title": clone_state.source_title,
                "kind": clone_state.source_kind,
            },
            "destination": destination,
        },
        "sync": {
            **copy,
            "cursor": clone_state.cursor,
            "discussion_cursor": clone_state.discussion_cursor,
            "more": more,
            "up_to_date": up_to_date,
            "participants": participants,
            **({"pinned": pinned} if pinned is not None else {}),
        },
    }


def _idle_document(clone_state: state.CloneState) -> dict:
    data = _document(
        clone_state,
        {"id": clone_state.destination_peer_id, "title": clone_state.destination_title},
        {
            "copied": 0,
            "skipped_unsupported": [],
            "forwarded": 0,
            "reuploaded": 0,
            "snapshots": 0,
            "topics_created": 0,
            "skipped_service": 0,
            "skipped_autoforward": 0,
            "reply_flattened": 0,
            "quote_flattened": [],
            "markup_dropped": [],
            "poll_votes": [],
        },
        more=False,
        participants=roster.fresh(clone_state),
        pinned=(
            None
            if clone_state.destination_kind == "forum"
            else pin.snapshot(clone_state)
        ),
        up_to_date=True,
    )
    data["remaining"] = False
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
