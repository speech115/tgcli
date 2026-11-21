# ADR-0128: Clone engine decisions

Status: Accepted

## Context

These decisions about channel cloning (`tgcli.clone`, `tg clone`) used to live as code comments beside the
code they explain. ADR-0132 removes comments from code, so the non-obvious
ones move here, grouped by module and by the function, class or name they
sit on. Each entry is the reason for a choice that the code alone does not
show; the code is the authority for what it does.

## Decision

### `src/tgcli/clone/attribution.py`

- `author_of`: An anonymous admin posting as the group ships neither `from_id` nor `post_author`; clients label it with the group's own title, so name the ambient chat rather than "id unknown" (issue #81). A broadcast source has no such chat and keeps the bare fallback.

### `src/tgcli/clone/comments.py`

- `degrade`: Same refusal shape as attribution._resolve / roster.collect (1.2.8): a linked group that turned private after init must not crash a sync whose posts have already copied. Degrade to the Honest marker; never join the source on the user's behalf. Clearing discussion_cursor / discussion_id_map is required: state.load rejects comments != enabled with leftover phase-2 progress.
- `sync_phase`: one shared ResolveContext spans every window of a sync run , and the discussion peers cannot change identity mid-run — later windows reuse the first window's entities instead of re-paying two GetChannels RPCs. Because that skips the per-window re-resolve, a group that turns private between windows now surfaces on the window's own reads — the iterator below carries the same degrade guard.
- `sync_phase`: Same: a flood, a 500 or a dead session stops the run, never degrades.
- `sync_phase`: --max-runtime bounds this leg as it bounds the posts leg: stop normally, the cursor stays where the next run resumes.
- `sync_phase`: stop before an anchor whose source post is newer than the posts cursor — but only while more posts may still arrive. Once the posts leg is exhausted, beyond-cursor anchors are orphans (deleted/never-seen) and must not stall the leg.

### `src/tgcli/clone/discussion.py`

- `PEER_UNAVAILABLE`: A peer id recorded in clone state can stop resolving for more reasons than a missing access hash: the account may have been removed from the channel, the channel deleted, or the account banned. Every recorded-destination lookup maps the whole set to a PolicyError, so none of them reaches the user as a raw Telethon traceback (the CLI contract).

### `src/tgcli/clone/idle.py`

- `account_id`: Telethon knows the id but kept no access hash for it, and then fails building the input peer; the full user is the way around.

### `src/tgcli/clone/init_peers.py`

- `init_discussion`: Only "none" (the source unlinked its group for a while) relinks; an "unavailable" degrade cleared the comment map while the destination group still holds the copied comments.
- `init_discussion`: posts copied before the link have no anchors, and there is no backfill, so a posts-only clone is never upgraded in place. A clone that was linked to this same group already has them.
- `init_discussion`: The same degrade as sync's: "unavailable" never relinks, and state holds no comment progress for a clone that is not copying comments.

### `src/tgcli/clone/lookup.py`

- `_SOURCE_PEERS`: The peer class a recorded source id belongs to, by the kind clone stored. Anything else clone supports (broadcast, megagroup, forum) is a channel.

### `src/tgcli/clone/pin.py`

- `sync_phase`: Crash-window recovery: a previous run died between the pin RPC and the state save, so the destination already carries exactly the pin this run intended to set. Adopt it as tgcli's own work — repair state, no mutation, no audit row (the CLI contract reserves "set" for a mutation this run performed). Strictly the exact intended id: any other pinned message — even a cloned post from ``id_map`` — is the owner's choice and still latches occupied.

### `src/tgcli/clone/progress.py`

- `PROGRESS_EVERY_BYTES`: A transfer line every ~5 MB: frequent enough that a large file never looks stuck, rare enough that a long sync does not flood a transcript.
- `SyncProgress.transfer`: The name is Telegram's, so it is what could carry a `\r` or an escape into a line CONTRACT.md §2 promises is plain.

### `src/tgcli/clone/quote_fallback.py`

- `FALLBACK_SOURCE_LABEL`: Source label for a quote that could not stay a native reply. Russian, to match the clones this tool actually runs; one place to change.

### `src/tgcli/clone/quotes.py`

- `FOREIGN_SEND_ERRORS`: A peer the account may not touch: the same answers Telegram gives to a probe and to a send that carries the peer as a reply target.
- `_peer_title`: An RPCError subclass meaning "ask again later", not "no title": caching that miss degrades every later quote and hides the wait.
- `_peer_title`: Match the plain channel id, never the -100-prefixed form: the source response can carry an unrelated chat whose id merely contains it.
- `_cross_leg_reply`: Discussion leg quotes a channel post: point at the destination's discussion anchor for that post (same walk as thread-root remap).
- `_cross_leg_reply`: Posts leg quotes a group comment: the id lives in the destination group, so the reply must name it. A later run's first posts window runs before the comments leg, so the group comes from clone state; the post is copied once, and flattening here would be for good.
- `_place_thread`: Only a comment has a thread root: a post's parent id names the channel, and the group's own message under that id may anchor an unrelated post.
- `_place_thread`: ``reply_to_top_id`` always names this group; ``reply_to_msg_id`` only when the header carries no other peer. Walking a foreign parent id here is The silent failure: the group has its own message under that id, and matching an anchor re-points the reply at an unrelated post.
- `_place_thread`: A resolved root is remembered in clone state, so later runs skip both anchor reads.
- `_place_thread`: The parent is lost (fallback or flattened), but the thread is known: keep the comment under its post, and keep counting the lost reply.
- `_unreachable_fallback`: A resolved entity already carries a display name; only the peers we could not open need the title read off the enclosing history response.

### `src/tgcli/clone/reforward.py`

- `SEARCH_LIMIT`: One messages.Search per reposted post against the flood budget. The window only has to cover the messages a single sender produced within one second; candidates outside `fwd_from.date` are discarded anyway.

### `src/tgcli/clone/replies.py`

- `_classify_header`: Discussion leg only: parent beyond the posts cursor means "not copied yet" → defer, unless the posts leg is already exhausted (parent will never arrive) → flatten. Parent behind the cursor and absent means permanently gone → flatten.
- `_classify_header`: Posts leg, the mirror: a comment beyond the discussion cursor is not copied yet, so the post waits for the comments leg, unless that leg stalled (it will not get there).

### `src/tgcli/clone/resolve.py`

- `DESTINATION_UNAVAILABLE`: What Telegram answers when the recorded destination cannot be opened by this account any more — deleted, left, or banned. ``channels.GetChannels`` raises these; Telethon's own ValueError covers a peer it cannot resolve at all.

### `src/tgcli/clone/reupload.py`

- `STILL_THUMB_SIZES`: The still-image sizes Telethon accepts back as a `thumb=` argument. A `PhotoPathSize` is an outline, a `VideoSize` is a video preview, and a `PhotoSizeProgressive` is not selectable by object, so none of them qualify.
- `PHOTO_EXTENSIONS`: Telegram takes an uploaded photo's format from its file name and refuses one without an image extension (PHOTO_EXT_INVALID). Telegram serves photos as JPEG.
- `uploaded_media`: A blur the source put on purpose is content: dropping it publishes the media uncovered.
- `uploaded_media`: Unset, Telegram may turn a video without sound into a GIF (as in `tg send --file`); a source GIF carries its own Animated attribute.
- `download_for_reupload`: Stale name/size (or unpredictable size): drop before re-download so the partial file and download_media see a free path. Telethon never overwrites: a file left at its name sends the next download to `src-<id> (1).jpg`.
- `download_for_reupload`: Stream into a sibling .part and rename, as `media download` does: the final name must mean "complete", or a killed run leaves a full-size sparse file the reuse check would upload as real media. Four chunk requests run at once (TDLib's non-Premium budget per DC), but only the contiguous prefix is checkpointed, so a FloodWait costs the chunks in flight instead of the whole file. Only this path reports bytes: a sub-chunk file is over before it could reach a progress mark.
- `download_for_reupload`: A stream that ended early is not a complete file, and the final name means complete. Keep the partial for the next run's resume.

### `src/tgcli/clone/roster.py`

- `_discussion_roster`: The roster runs after the copying is done; a refusal here is a marker, never an exception out of a finished sync.
- `REFRESH_S`: The sidecar's own mtime says when the roster was last written, so no state records it.
- `collect`: A deferral keeps the last full snapshot and its age, so the next run retries instead of waiting out `REFRESH_S`.

### `src/tgcli/clone/send.py`

- `RunPace.__init__`: One slot for every send: Telegram counts the messages that land, whichever method put them there.
- `reupload_batch`: An unprotected source lets Telegram reuse its own file: no bytes move, and a sticker stays a sticker. Any refusal but a flood falls back to the byte copy below.
- `reupload_batch`: A timeout or 500 leaves the send's outcome unknown: reuploading could post the message twice.
- `uploaded`: The send carries the blur; UploadMedia's echo need not keep it.
- `CAPTION_LIMIT`: The caption limit without Premium. A Premium author may post up to 4096, so a copy by an account without it is refused MEDIA_CAPTION_TOO_LONG.
- `_unavailable_emoji`: Telegram answers a gone emoji with DocumentEmpty, not by omitting it.
- `forward_batch`: A proven original outranks the attribution prefix, because forwarding it carries Telegram's own header instead of describing one.
- `forward_batch`: Protected content is refused by reference, so only bytes copy it.
- `forward_batch`: After the save: a failed rest must not leave the post unmapped and copied again by the next run.
- `forward_batch`: Only a confirmed send *and* a durable mapping save may clear the cache: an incomplete confirmation or a crash before the save must leave the downloaded bytes for the next run's resume.

### `src/tgcli/clone/snapshot.py`

- `retract_vote`: A FloodWait is not special here: propagating it would kill the run before the poll_votes tail and leave the vote standing silently.
- `render`: A private/deleted peer is a missing author label, never a failed sync.

### `src/tgcli/clone/state.py`

- `_PEER_CLASS`: Peer class per source kind: a User/Chat/Channel can share a bare numeric id; not split further, since a megagroup toggling into a forum keeps its channel_id and must stay one slot.
- `CloneState`: Last name the destination was seen under, recorded by clone init and clone sync so the offline `clone status` can say which channel a clone writes to (a private destination has no username to look up). Null until the clone is next touched by a command that resolves the peer.
- `CloneState`: When a FloodWait this clone could not ride out ends. `clone sync` writes it on exit 5, refuses before any request while it lies in the future, and clears it on a completed run. An older flood budget introduced the field.
- `CloneState`: Source discussion anchor id -> destination anchor id, so a later run places a comment without re-reading either anchor.
- `CloneState`: Source message -> the message carrying the rest of its split caption. Not copies of source messages, but the clone's own: the tail guard counts them (`max_destination_id`).
- `CloneState`: Per leg kind, the send about to happen: ``{"kind": "batch" | "rest", "sources": [source ids]}``. Saved before the send and cleared by the save that maps it, so one left behind names what a crashed run sent (``clone.journal``).
- `CloneState`: Map writes since the last save; ``save`` applies them in order. A state that was never saved starts empty, so its pending list is its whole map.

### `src/tgcli/clone/statedb.py`

- `_MIGRATIONS`: Forward-only, one entry per schema version above 1. A clone database holds work that cost real Telegram traffic to produce, so a schema addition migrates the file in place; only a *newer* file than this build understands is refused, because reading it would silently drop whatever it added.
- Source discussion anchor -> destination anchor. Not UNIQUE on dest: an album post's anchors all lead to the one destination thread.
- Source message -> the message that carries the rest of its split caption, per leg, so the next run's tail guard knows it as the clone's.
- Per leg, the batch or caption rest about to be sent, so a run that crashed between Telegram's send and the mapping save is adopted.

### `src/tgcli/clone/sync.py`

- `_open_slot`: Warn before the work, not only after it: a run killed by FloodWait never reaches the tail, so the resume is where the operator sees this.
- `_Run.__init__`: A post replying to a comment not copied yet waits for the comments leg (`posts_waiting`), unless that leg has stalled and will not reach it this run. A stall releases only the batch that waited (`release_waiting`): the posts after it may reply to comments that copying it unblocks.
- `_Run.copy_legs`: alternate posts×WINDOW with comments until both exhaust. Enter comments only while --limit budget remains; a limit hit during posts ends the run (same as today for limit < WINDOW).
- `_Run.copy_legs`: The group refused mid-run and comments degraded: the remaining posts copy alone.
- `_Run.copy_legs`: A waiting post needs the comments leg to move. If this window did not move it, no later one will: the post goes out flat.
- `_Run.copy_batch`: `source` is this leg's own entity — the channel for posts, the discussion group for comments — so the ~total describes the work the leg's counter is counting (#174).
- `_Run.copy_batch`: Nothing was sent: the cursor stays and the callers' cap checks end the run as a normal wall-clock stop.
- `_Run.copy_batch`: Only a native forward carries a keyboard; every other transport rebuilds the message and Telegram will not let a user account reattach one, so the rows are recorded as lost. Every loss is announced as it happens, not in the tail: the copy is already permanent and never revisited, and the run that dies on a flood mid-way leaves no result document to carry the rest.
- `_finish`: A run the cap stopped stopped early: no pin phase and no roster listing (the CLI contract), and `remaining` reflects the copy, not later phases.
- `_finish`: --max-runtime exhausted: a normal stop, not an error — the cursor advanced and the next invocation resumes.

### `src/tgcli/clone/transport.py`

- `decide`: Forum placement-only headers on a forum destination are not a lost reply.

### `src/tgcli/commands/clone.py`

- `list_clones`: An unreadable entry has no created_at at all (the CLI contract: every field but clone_id is null), so it sorts ahead of every dated one.
- `_load_for_commit`: Always a secret suffix: the bare prefix is computable from public ids, so anyone could title a group with it and hand the account its ownership to be adopted. Saved before creation, so a crashed init still finds its own half-created peers; a --replace never re-adopts the previous init's.
- `_apply_ergonomics`: CONTRACT §4: a genuine flood is exit 5 with retry_after, and a 500 an error, never a quiet "muted: false" on a successful init.
- `_apply_ergonomics`: Muting the clone and filing it into the "Clone" folder change the account's own settings: audited before they leave, like every init write.

## Consequences

A change that breaks one of these reasons updates or removes its entry in the
same PR. A new decision of this kind is added here, not as a code comment.
