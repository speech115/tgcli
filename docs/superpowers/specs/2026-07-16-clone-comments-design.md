# Clone — Channel Comments (linked discussion group), round 3

Date: 2026-07-16
Status: implemented (2026-07-17)
Sequenced after: round 2 (`2026-07-16-clone-chat-types-2-design.md`, currently on
`codex/clone-chat-types-round-2-*` branches) merges to main. Round 2's
`destination_kind` + `topic_map` establish the "second mapping structure in
state" precedent this design builds on.

## Problem

`tg clone` copies a broadcast channel's posts but ignores its comment section.
Comments live in a separate linked discussion megagroup (`ChannelFull.
linked_chat_id`): Telegram auto-forwards every channel post into that group,
and comments are ordinary group messages replying to that auto-forward
(`reply_to_top_id` = the auto-forward's id, resolved via
`messages.getDiscussionMessage`). Every clone design to date declared
comments out of scope (clone-design spec Non-goals, ADR-0021 §out-of-scope);
the only prior thinking was in the deleted mirror feature (ADR-0014 slice 3,
never implemented).

Forum topics are a different mechanism and stay in round 2. Live-proven
constraint (ADR-0015): Telegram refuses a forum megagroup as a discussion
group — comments and topics are mutually exclusive server-side, not a tgcli
policy choice. Plain megagroups need nothing here: their reply chains already
clone under ADR-0021.

## Goals

- A cloned channel whose source has a readable linked discussion group gets a
  real comment section: private owned discussion megagroup, linked before the
  first post is copied, with comment threads attached to the right posts.
  Acceptance is visual: the clone shows a working "comments" button per post
  and the threads match the source.
- The discussion group is cloned whole — threaded comments *and* off-thread
  chatter — under the existing megagroup transport/attribution rules.
- Order fidelity holds per chat: posts strictly oldest→newest (existing
  cursor), discussion messages strictly oldest→newest (new second cursor).
  A comment is never copied before its parent post.
- Attribution upgrade (global, all clone kinds — amends ADR-0021 prefix
  format): the author prefix identifies the person, not just displays a name.
  Fallback ladder, first hit wins:
  1. entity resolves, has username → `"Name (@username): "` (`@username`
     self-links in any client);
  2. entity resolves, no username → `"Name: "` with a `MentionName` text_mention
     entity on the name (clickable profile link; resolves for the clone owner,
     whose account fetched the entity during sync);
  3. entity unresolvable (deleted account) → `"id 123456: "` plain text;
  4. no sender at all → `post_author` signature text if present, else
     `"id unknown: "`.
  Nothing is fabricated; degradation stays visible.

## Non-goals

- Forum topics (round 2 owns them). Basic groups, bots — likewise round 2.
- Retroactive comments for existing clones. Telegram creates the auto-forward
  anchor only at post-send time with the link already in place; there is no
  API to backfill anchors. Existing clones stay posts-only; to get comments the
  user runs a fresh `init` (new destination pair).
- Watch mode: comment edits/deletions after sync are not tracked (clone is
  one-shot, unchanged).
- Auto-joining the source discussion group. Never act on the source side.
- Opt-out flag. Comments are automatic when the source has them; a
  `--no-comments` escape hatch is added only on demonstrated pain (giant
  comment volumes), not speculatively.

## Behavior

### Init

1. Resolve source; `channels.getFullChannel` → `linked_chat_id`.
2. No linked chat → today's behavior, nothing new.
3. Linked chat present but its history unreadable (private group, not a
   member) → clone the channel posts-only; record
   `comments: "unavailable"` in state; `clone status` and init/sync output
   carry the marker permanently. No error, no silent loss. Joining the group
   and re-initing later produces a comments-enabled clone.
4. Linked chat readable → create a private owned megagroup (marker-titled,
   same crash-recovery scheme as the channel: null peer id + marker scan),
   copy the discussion group's title/about/avatar, then
   `channels.SetDiscussionGroupRequest(clone_channel, clone_group)` —
   **strictly before the first post is synced**, otherwise anchors never
   exist. Link state is persisted; a crash between create and link is
   recovered by re-running the link (idempotent) on next invocation.

Note on pacing: init now creates two peers per clone. The live FLOOD_WAIT
(~15 h) on rapid peer creation applies double; keep the existing cooldown
discipline and reuse recovered peers aggressively.

### Sync — two phases per run

Phase 1 — channel posts, exactly as today (cursor, id_map, hybrid transport).

Phase 2 — discussion group, oldest→newest with its own cursor:
- Source auto-forwards are recognized (forward header from the linked source
  channel anchoring a thread) and **skipped** — the destination has its own
  auto-forwards, created by Telegram when phase 1 sent the posts. Counted as
  `skipped_autoforward`.
- Comments: remap the thread anchor — source `reply_to_top_id` → source
  channel post (via the source anchor's forward header) → destination post
  (channel `id_map`) → destination anchor
  (`messages.getDiscussionMessage(dest_channel, dest_post_id)`, cached per
  run). `reply_to_msg_id` (comment-on-comment) remaps through the discussion
  `id_map`. Send as reply into the destination group; Telegram renders it in
  the thread. Unmappable parents flatten with the existing `reply_flattened`
  semantics.
- Off-thread group messages clone as plain megagroup messages (ADR-0021
  rules).
- All existing per-batch guarantees carry over: state saved after confirmed
  batch, tail verification (now on both destinations), FloodWait → exit 5,
  `--limit N` counts batches across both phases; when the limit lands inside
  phase 2, comments lag posts until the next run — accepted.

### State (extends the clone JSON file)

New fields: `discussion_source_peer_id`, `discussion_destination_peer_id`
(null until created/adopted), `discussion_linked` (bool),
`discussion_cursor`, `discussion_id_map`, `comments`
(`"enabled" | "unavailable" | "none"`). Anchor lookups are an in-run cache,
not persisted (recomputable via `getDiscussionMessage`). Same no-migration
policy: version bump, old files rejected with a clear message.

## Module layout & budgets

> Layout note (2026-07-16 deepening refactor): the phase-1 batch machinery is
> now `clone/batching.plan` (pure event generator) + `clone/transport.decide`
> (pure TransportPlan). The phase-2 discussion loop should consume these
> interfaces instead of duplicating the sync loop; file budgets in this
> section predate the refactor.

- `src/tgcli/clone/discussion.py` (new, ≤120 lines): linked-chat detection,
  destination group create/link/recover, anchor resolution + cache,
  auto-forward recognition.
- `clone/attribution.py`: prefix ladder + `prefixed()` learns to emit a
  `MentionName` entity (still shifting UTF-16 offsets). ≤ +30 lines.
- `clone/state.py`: budget 170 → 190 (on top of round 2's 170).
- `commands/clone.py`: phase-2 loop reuses the phase-1 batch machinery;
  budget 400 → 460. Exceeding a budget requires cutting before adding.

## Testing

- Mocked, black-box via `main([...])`: init creates+links group before first
  post; unreadable group → posts-only + `comments: unavailable`; anchor remap
  happy path; comment-on-comment; unmapped anchor → `reply_flattened`;
  auto-forward skip counter; prefix ladder incl. text_mention entity offsets;
  crash between group-create and link → recovery relinks.
- Live acceptance gate (visual, as always): clone a real channel with an
  active comment section; verify the comments button appears, thread contents
  and order match, author prefixes are clickable where promised, rerun is
  idempotent (0 copied).

## Documentation

- New ADR (number after round 2's ADR-0022): comments design, the
  anchor-timing constraint, the global prefix-format amendment to ADR-0021.
- CONTRACT.md §11: new counters (`skipped_autoforward`), `comments` field in
  status/init/sync output.
- MAP.md, PLAN.md, DEVLOG.md in the same change as the code.

## Decisions log (2026-07-16, resolved with the user via grilling)

1. Scope = broadcast channel + linked discussion group. Groups per se have no
   comments; forum topics stay in round 2.
2. Full-fidelity clone of the discussion (real linked group, real threads),
   not a text snapshot.
3. New clones only; no migration machinery for existing clones (re-init to
   opt in). Rejected: partial migration (half-commented channel looks like a
   bug) and full re-send migration (re-clone in disguise).
4. Automatic when the source has a discussion group; no flags.
5. Two-phase sync with independent cursors; time-interleaving rejected
   (destroys the cursor model for no visible gain).
6. Attribution inherits ADR-0021 megagroup rules; prefix format amended
   globally to the identify-the-author ladder above (user explicitly wants
   username/profile link, not just a display name). The prefix change is
   separable and may land as a small standalone slice before round 3.
7. Unreadable source discussion group → posts-only clone + persistent honest
   `comments: unavailable` marker. Rejected: hard PolicyError (dead end with
   no opt-out) and auto-join (visible action on the source side).
8. Sequencing: implement as round 3 after round 2 reaches main (same files:
   state.py, replies.py, attribution.py, clone.py).

## Live probe findings (2026-07-16, read-only, @disruptors_official post 3680)

Raw `channels.getFullChannel` / `messages.getDiscussionMessage` /
`messages.getReplies` against a real commented channel:

- **Auto-forward recognition rule (open question 1 — resolved).** The anchor
  message in the discussion group carries
  `fwd_from.saved_from_peer == <source channel>` and
  `fwd_from.saved_from_msg_id == <source post id>` (also `channel_post` set).
  Match on `saved_from_peer` + `saved_from_msg_id`; this same pair *is* the
  source-anchor → source-post mapping, no extra `getDiscussionMessage` call
  needed on the source side.
- **Gotcha: direct comments have `reply_to_top_id = null`.** A comment
  replying straight to the anchor has only `reply_to_msg_id = <anchor id>`;
  `reply_to_top_id` is set only on nested (comment-on-comment) replies.
  Thread-membership logic must treat `reply_to_msg_id == known anchor` as the
  thread root case, not rely on `top_id` being present.
- **Gotcha: comments with `from_id = null` occur in the wild** (observed on a
  regular comment). The attribution ladder's "no sender" steps
  (`post_author` → `id unknown`) will fire in real discussion groups, not
  just in theory.
- Reply quotes appear on comments (`quote: true` + `quote_text`) — the
  existing `InputReplyToMessage` quote passthrough in `clone/replies.py`
  covers this shape.
- **Gotcha: `linked_monoforum_id` is not `linked_chat_id`.** Channels with
  the new direct-messages feature (monoforum, observed on @groks) expose
  `linked_monoforum_id` while `linked_chat_id` stays null. Monoforums are not
  comment sections; detection must read only `linked_chat_id`.
- `messages.getReplies` via raw Telethon requires all positional args
  (`offset_id`, `offset_date`, `add_offset`, `max_id`, `min_id`, `hash`).

## Open questions

- Whether `--limit` should split its budget between phases or run phase 1 to
  exhaustion first — **resolved**: sequential, phase 1 first. `--limit N`
  counts batches across both phases; phase 1 runs to exhaustion before phase
  2 starts, and a run that stops inside phase 2 leaves comments lagging
  posts until the next invocation (`docs/superpowers/plans/2026-07-17-clone-comments.md`,
  ADR-0023).
