# ADR-0023: Clone channel comments via the linked discussion group

Date: 2026-07-17
Status: accepted
Builds on: ADR-0017 (clone), ADR-0021 (attributed sources, amended here),
ADR-0022 (forum destinations, topic map, `Leg`-shaped seam precedent).
Spec: docs/superpowers/specs/2026-07-16-clone-comments-design.md

## Decision

- A cloned broadcast channel whose source has a readable linked discussion
  group (`ChannelFull.linked_chat_id`) gets a second, tool-created private
  owned megagroup, linked with `channels.SetDiscussionGroupRequest` strictly
  before the first post is synced — otherwise Telegram never creates the
  per-post auto-forward anchors comments hang off. `linked_monoforum_id` is
  a direct-messages monoforum, not a comment section, and is never read
  (live-proven on @groks).
- `channels.TogglePreHistoryHiddenRequest(enabled=False)` runs before the
  link, matching the shape Telegram requires of a discussion group.
- **Anchor-timing constraint, no retroactive backfill.** Telegram creates the
  auto-forward anchor only at post-send time with the link already in place.
  There is no API to backfill anchors for a clone that already has posts, so
  existing posts-only clones stay posts-only forever; the only way to get
  comments is a fresh `init` against a new destination pair (new
  `clone_id`, since `clone_id` is a hash of account + source peer, not of the
  destination).
- **`comments: "unavailable"` is a permanent honest marker, not a
  `PolicyError`.** A linked group that exists but is unreadable (private,
  not a member) does not fail init — it clones the channel posts-only and
  records the marker forever in state, `clone status`, and every `init`/
  `sync` response. Joining the source group and re-initing later is the only
  way to upgrade it; tgcli never joins on the user's behalf (never acts on
  the source side).
- **Two-phase sync, sequential, phase 1 first.** `sync` now runs phase 1
  (channel posts, unchanged) to exhaustion, then, only if it did not stop on
  `--limit` and `comments == "enabled"`, runs phase 2 (the discussion group)
  against the same remaining batch budget. This resolves the spec's one open
  question: `--limit` does not split across phases. A run that stops inside
  phase 2 leaves comments lagging posts until the next invocation — accepted,
  not treated as a defect.
- **Auto-forward recognition and skipping.** Phase 2 walks the discussion
  group oldest-to-newest with its own cursor (`discussion_cursor`) and
  recognizes Telegram's own auto-forwards of channel posts
  (`fwd_from.saved_from_peer == source channel` and
  `fwd_from.saved_from_msg_id == source post id`) without ever copying them
  — the destination group already has its own anchors, created by Telegram
  when phase 1 sent the posts. Recognized anchors are counted in
  `skipped_autoforward` and only ever read, never mutated. A channel ALBUM
  post auto-forwards into the linked group as an album too, so anchor
  recognition works per-batch (`_anchor_posts` in `clone/comments.py`): a
  batch is either entirely anchors or entirely content, and a batch that
  mixes the two is a `PolicyError` (`clone discussion anchor album is
  incomplete`) rather than a silent partial skip.
- **Thread-anchor remap.** A comment's parent chain is re-pointed onto the
  destination's own anchor: source `reply_to_top_id` (falling back to
  `reply_to_msg_id` for a direct reply, since Telegram sets
  `reply_to_top_id = null` on a direct comment — thread-root detection keys
  off `reply_to_msg_id == known anchor`, never off `top_id` being present) →
  source post (via the cached anchor lookup) → destination post (`id_map`)
  → destination anchor (`messages.GetDiscussionMessageRequest`, cached per
  run in `clone/discussion.anchor_for`). A destination comment's reply
  header is rebuilt against the destination anchor from scratch, so the
  source comment's quote (`quote_text`/`quote_entities`/`quote_offset`) is
  carried across explicitly in `clone/comments._remap` — it is not implied
  by the new `InputReplyToMessage`. Comment-on-comment replies remap through
  `discussion_id_map` the same way ordinary megagroup replies remap through
  `id_map`. An unmappable parent (unmapped post, or `getDiscussionMessage`
  returns no anchor) leaves the plan alone and flattens on the existing
  `reply_flattened` rule — comments are not held back waiting for a parent
  that will never resolve.
- **Off-thread group chatter** (messages in the discussion group that are
  not replies to a recognized anchor) clones as a plain megagroup message
  under the unmodified ADR-0021 transport rules: `transport.decide` sees
  `source_kind = "megagroup"` and applies `drop_author=False` native
  forwarding or reupload exactly as any megagroup clone would. Comments
  themselves travel through the identical path — a comment is not a special
  transport mode, only a reply-header remap ahead of the existing decision.
- **Attribution ladder amended globally (ADR-0021 prefix format), not just
  for comments.** `author_name` becomes `author_of`, returning
  `Author(text, mention_user_id)` instead of a bare string. Ladder, first
  hit wins: (1) resolvable entity with an active username →
  `"Name (@username)"`, no mention entity — `@username` self-links in any
  client; (2) resolvable `types.User` without a username → `Author(text=
  name, mention_user_id=entity.id)`, rendered as a `MessageEntityMentionName`
  profile-link entity at offset 0; (3) resolvable non-user sender (channel
  sender, anonymous admin) without a username → plain name, no mention
  entity (`MentionName` only takes a user id); (4) unresolvable entity →
  `"id <sender_id>"`; (5) no sender at all → the post's `post_author`
  signature if non-empty, else `"id unknown"`. `from_id = None` occurs on
  real comments in the wild (live-proven), so the no-sender branch is a real
  code path, not a theoretical one. This lands as its own commit
  (`57c3e84`) ahead of the comments feature and applies to every clone kind
  that prefixes text, not only comments.
- **Module layout deviates from the spec's budget table.** The spec put
  detection, linking, anchor lookup, and the phase-2 sync loop all in one
  `discussion.py`. Shipped as three files instead:
  - `clone/discussion.py` (≤120 lines): pure/near-pure detection
    (`linked_chat_id`, `is_discussion_destination`, `autoforward_post_id`),
    destination group create/adopt/link (`adopt`, `ensure_linked`), anchor
    lookup with an in-run cache (`anchor_for`), and the shared tail-verify
    helper both legs use (`verify_tail`).
  - `clone/comments.py` (new, phase-2 sync leg): `sync_phase`, the
    discussion-group batch loop, source-anchor caching, and the reply-header
    remap (`_remap`, `_anchor_posts`).
  - `clone/legs.py` (new, ≤60 lines): the `Leg` seam — `posts(clone_state)`
    and `discussion(clone_state)` each return a `Leg` exposing
    `source_kind`/`destination_kind`/`dest_for`/`record_mapping`/`cursor`/
    `clone_id` over either the post fields or the discussion fields of
    `CloneState`. `batching.plan`, `transport.decide`, and
    `replies.target` already only read those five things off `CloneState`
    (round-2/deepening-refactor precedent), so they take a `Leg` in place of
    a `CloneState` with no logic change. This is what lets `commands/
    clone.py`'s single `copy_batch` closure drive both phases through the
    same batch machinery instead of a duplicated sync loop.
  - Splitting kept every file inside its budget and kept `state.py` a pure
    data module (fields, validation, mapping accessors) with no sync-loop
    logic in it. `clone/state.py` grew 150 → 189 lines (spec allowed 190);
    `commands/clone.py` grew to 449 lines (spec allowed 460).
- **State**: `VERSION = 2`; old state files are rejected by the existing
  version check with its existing message — there is no migration path, by
  design (round-2 precedent). New `CloneState` fields: `comments` (`"none"`
  \| `"unavailable"` \| `"enabled"`), `discussion_source_peer_id`,
  `discussion_destination_peer_id`, `discussion_linked`,
  `discussion_cursor`, `discussion_id_map`. Validation is fail-closed the
  same way `topic_map` is: `comments == "enabled"` requires
  `discussion_source_peer_id` and `source_kind == "broadcast"`;
  `comments != "enabled"` forbids a non-empty `discussion_id_map` or a
  non-zero `discussion_cursor`; `discussion_linked` implies
  `discussion_destination_peer_id is not None`. `max_destination_id()` (used
  by tail verification on the channel) is unchanged and does not include
  discussion ids — they live on a different peer entirely, checked instead
  by `max_discussion_destination_id()`.

## Consequences

A cloned channel with an active source comment section gets a real,
independently-threaded discussion group instead of silently dropping
comments. The cost is a second peer per comments-enabled clone (double
exposure to the live ~15h FLOOD_WAIT on rapid peer creation — init now
creates up to two channels/groups per run) and a second cursor/tail to
verify every sync. Existing posts-only clones cannot be upgraded in place;
users who want comments re-init against a fresh destination.

## Out of scope

Retroactive comments for existing clones (no backfill API — permanent, not
a "not yet"). Watch mode / tracking comment edits or deletions after sync
(clone stays one-shot). Auto-joining the source discussion group. A
`--no-comments` opt-out (comments are automatic when the source has them;
an escape hatch is added only on demonstrated pain, not speculatively).

## Live evidence

Read-only live probe against @disruptors_official post 3680 (recorded in
the spec) established the auto-forward recognition pair, the
`reply_to_top_id = null` direct-comment shape, `from_id = null` on real
comments, and `linked_monoforum_id != linked_chat_id`. The end-to-end
mocked suite was 454 passed, 8 skipped at that point.

The full end-to-end live acceptance gate (real source channel + discussion
group, comment threads through the clone, idempotent rerun) **passed on
2026-07-17** against account `main`, using a fixture built by hand for the
gate (no reusable comments fixture existed; a forum megagroup cannot stand
in — ADR-0015). Full results, including the id-shift proof that the anchor
remap did real work, are recorded in
`docs/superpowers/plans/2026-07-17-clone-comments.md` under "Live results
(2026-07-17)".

## Live findings (2026-07-17 gate)

The gate found and fixed four bugs, all invisible to the mocked suite
because they share one shape: **Telegram answers a no-op mutation with an
error, not silence.** A mock records the request and never objects; the
live API does. Any future Telegram write path in this codebase should
assume its "already in the desired state" case answers with a distinct
error code, not a 2xx no-op, and handle it explicitly rather than relying on
review or mocked tests to catch it.

1. **Album anchors** (`7b8cb6c`, found in review before the gate ran): a
   channel ALBUM post auto-forwards into the linked group as an album, so
   its anchor arrives as a multi-message batch; recognition gated on
   `len(messages) == 1` re-copied every album anchor as content instead of
   skipping it. The same commit fixed a direct comment's rebuilt reply
   header dropping its quote.
2. **`ChatNotModified` / `LinkNotModified`** (`8a4a59e`): on a megagroup
   Telegram just created, `TogglePreHistoryHiddenRequest(enabled=False)`
   answers `ChatNotModified` — init crashed on every single run, before ever
   linking. `SetDiscussionGroupRequest` on an already-linked pair answers
   `LinkNotModified`, so the designed idempotent-relink crash recovery
   (§Decision, `ensure_linked`) was not actually idempotent until this fix.
3. **Sync before linking is unrecoverable data loss** (`2438940`): a
   FLOOD_WAIT on init's second peer can leave `comments: "enabled"` in state
   with no linked group. `sync` would then send posts Telegram will never
   anchor — there is no backfill API, so the clone loses its comments
   permanently and silently. `sync` now refuses to run before
   `discussion_linked` and sends the user back to `init`. This was rated
   roughly 60% confidence by the code review that preceded the gate and was
   not reported as a finding; it occurred on the very first live run. It is
   not a rare interruption — it is the normal outcome of a FLOOD_WAIT during
   init, which this ADR's Consequences section already flags as doubly
   likely now that init creates two peers.
4. **`ChatAboutNotModified`** (`1722b5b`): `EditChatAboutRequest` with
   unchanged text answers `ChatAboutNotModified` — init died on its own
   documented crash-recovery path when re-run against an already-correct
   description.

**Live FLOOD_WAIT cost:** creating two peers per init hit repeated
FLOOD_WAIT during the gate (23s, 5s, 349s, 335s), each retry consuming the
committed preview and requiring a fresh `clone init` preview. Progress
persisted across waits via the recorded peer ids; crash recovery adopted
both peers with no duplicates.
