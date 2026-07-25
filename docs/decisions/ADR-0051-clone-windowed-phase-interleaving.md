# ADR-0051: Windowed interleaving of the clone's posts and comments legs

Date: 2026-07-25
Status: accepted

Amends [ADR-0023](ADR-0023-clone-channel-comments.md) — "two-phase sync,
sequential, phase 1 first" — in the ordering clause only. Anchor
recognition, the `discussion_cursor`, and the leg seam are unchanged.

## Context

ADR-0023 runs phase 1 (channel posts) to exhaustion before phase 2 (the
discussion group) begins, and explicitly accepted "a run that stops inside
phase 2 leaves comments lagging posts" as not a defect. A long protected
clone has since shown two costs of that ordering.

**The intermediate state is not merely incomplete, it is misleading.** A
channel with a linked discussion group has every post auto-forwarded into
the group by Telegram; those anchors are what comments hang from. Until
phase 2 runs, the clone's group therefore holds anchors and nothing else,
so it reads as a pointless duplicate of the channel. Observed live on
`[икона]` (`t.me/c/3514350021`), where the owner reported it as duplicated
posts; the source group shows the identical anchors interleaved with real
comments and reads correctly. Nothing is wrong with the copy — the missing
half is what makes the present half look wrong.

**A stop is the normal outcome, not the exceptional one.** On a protected
source every message takes the download+reupload path, and FloodWait is
routine (`[икона]`: 14 FloodWait exits, 160.6 s/message, still at post 83
after two hours). Under ADR-0023's ordering the expected result of an
interrupted long clone is *all posts, zero comments* — the worst possible
partition of the work, because comments are the half that cannot be
inferred from the source by a reader of the clone.

The dependency that forced the ordering is weaker than the ordering. A
comment needs its **own** parent post mapped, plus that post's anchor in
the destination group — not every post. But relaxing the order naively is
unsafe: `replies._classify_header` maps an unmapped cross-leg parent to
`flatten` (`clone/replies.py:134-136`), so a comment whose post is not yet
copied would not be deferred, it would be planted flat, permanently, in the
destination. `comments.sync_phase` states the invariant it relies on in its
own docstring: "Phase 1 has already run to exhaustion, so every parent post
is mapped."

## Decision

1. **Sync alternates in windows.** `sync_text` runs the posts leg for at
   most `WINDOW` batches, then the comments leg up to the bound below, then
   repeats until both legs are exhausted. `WINDOW = 50` batches, a
   constant, not a flag (ADR-0045 economy: a knob invites tuning loops).

2. **The comments bound comes from the anchor scan, free.** Phase 2 already
   walks the group oldest-to-newest recognising anchors
   (`clone/comments.py:80-83`). It now stops when it meets an anchor whose
   source post id is newer than the posts cursor. Everything before that
   anchor is safe by construction: a comment cannot precede the anchor it
   replies to, so every parent in the scanned prefix is already mapped.
   Comments written later against older posts simply fall into a later
   window — they lie further along the group's id order.

3. **An unmapped parent defers; it never flattens.** For the discussion
   leg, a cross-leg parent that resolves to no destination post stops the
   leg at that batch instead of degrading to `flatten`. This is the
   safety condition for decisions 1–2, and it is required even if the
   bound is believed airtight: flattening is written to a real chat and
   cannot be undone. A genuinely unmappable parent — a source post
   deleted or skipped as unsupported — is a different case and keeps
   today's `flatten` behaviour, distinguished by whether the post id is
   beyond the cursor (defer) or behind it and absent (flatten).

4. **Cursors and resume are untouched.** Both legs keep a single monotonic
   integer cursor. A window boundary is not a checkpoint and needs no
   state field; an interrupted run resumes exactly as it does today, and a
   state file written by an older version stays valid.

5. **`--limit` keeps counting batches across both legs.** ADR-0023 resolved
   that `--limit` does not split across phases; with interleaving it now
   naturally spans them, and `sync.more` keeps its meaning. This is the
   one observable behaviour change: a `--limit 5` run may now return
   comments where it previously returned only posts.

## Consequences

- An interrupted clone leaves a coherent prefix — posts with their
  discussion — instead of a complete channel with a silent group. Partial
  output becomes readable rather than misleading.
- Ordering *within* each leg is unchanged: both scans stay monotonic by
  source id, so the destination's message order is identical to today's.
  Only the interleaving of the two streams changes.
- Flood exposure is materially unchanged — same RPCs, same peers, same
  transports. The added cost is re-entering phase 2 per window, which
  re-runs `discussion.verify_tail` (one `GetHistory`). `WINDOW = 50` keeps
  that under ~2% of a window's request count; a small window would not.
- `verify_tail` already tolerates repeated entry (it skips anchors), so
  multiple phase-2 entries per run need no new allowance.
- The comments leg gains a stopping condition it did not have, so a bug
  there can now stall comments silently rather than flatten them loudly.
  The defer path must therefore be visible in the sync JSON — the existing
  `discussion_cursor` already exposes it, and progress lines (ADR-0049)
  announce each `comments` phase entry.
- This does not fix attribution loss on reposted comments; that is
  [ADR-0050](ADR-0050-clone-forward-attribution.md) and independent.
