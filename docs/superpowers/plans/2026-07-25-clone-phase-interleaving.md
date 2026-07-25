# Windowed interleaving of the clone's sync legs — Implementation Plan

> **For agentic workers:** execute tasks in order, checkbox (`- [ ]`)
> syntax for tracking. Scope is fixed by
> [ADR-0051](../../decisions/ADR-0051-clone-windowed-phase-interleaving.md),
> which amends [ADR-0023](../../decisions/ADR-0023-clone-channel-comments.md)
> in its ordering clause only. One PR, one tagged patch release (ADR-0038).
> Independent of [ADR-0050](../../decisions/ADR-0050-clone-forward-attribution.md) —
> either may land first.

**Goal:** An interrupted `clone sync` leaves a coherent prefix — posts with
their comments — instead of every post and no discussion. The clone's
group stops looking like a pointless duplicate of the channel while it is
still filling.

**Non-goals:** a window-size flag or config key; changing the order within
either leg; changing cursors, state shape, or JSON; per-post interleaving
(it would need `GetRepliesRequest` per post — more RPCs, rejected in
ADR-0051's reasoning).

## Global constraints

- TDD; no network in tests; boundary tests assert exact Telethon request
  types and arguments (AGENTS.md).
- CONTRACT.md updates land in the same commit as the code.
- `scripts/gate.sh` before every commit; raise flagged ceilings to
  measured size only.
- PR → `reviewer` subagent → green CI (PR-event run) → merge → tag per
  `docs/agents/release.md`.
- No live Telegram call at any point in this plan. Live acceptance on
  `[икона]` is owner-gated and happens after merge, if at all.

## Ordering note

Task 1 is the safety condition and **must land before** task 2. Merged in
the other order, the first window would flatten every comment whose post is
not yet copied — permanently, in a real chat. Do not reorder these.

### 1. Defer, never flatten, an unmapped cross-leg parent

- [x] Test (`tests/test_clone_replies.py`): discussion-leg comment whose
      parent post id is **beyond** the posts cursor → classified as
      deferred, not `flatten`.
- [x] Test: comment whose parent post id is **behind** the cursor and
      absent from the map (deleted or skipped-unsupported source post) →
      still `flatten`, exactly as today. This is the discrimination the
      whole change rests on; assert both directions in one test module.
- [x] Test (integration): a deferred batch leaves `discussion_cursor`
      unchanged and sends nothing, and the next run picks it up once the
      post is mapped.
- [x] Implement in `clone/replies.py` + `clone/comments.py`. The posts
      cursor must reach the classifier — pass it, do not read state
      globally.

### 2. Window the two legs

- [ ] Test (`tests/test_cli_clone_sync.py`): a source with more posts than
      `WINDOW` and comments on early posts → the request order is
      posts×WINDOW, then comments for those posts, then the next posts
      window. Assert on the recorded request sequence, not on counts
      alone.
- [ ] Test: a source smaller than one window produces exactly today's
      request sequence — the common small clone is byte-identical.
- [ ] Test: `comments == "disabled"` / `"none"` → no interleaving, no
      phase-2 entry, unchanged behaviour.
- [ ] Test: interrupting mid-window (FloodWait on the first send of window
      2) leaves window 1's posts **and** their comments durably mapped,
      and both cursors resumable.
- [ ] Implement `WINDOW = 50` as a module constant in `clone/legs.py`
      (the seam that already owns both legs); loop in `sync_text`.
      Keep `commands/clone.py` growth minimal.

### 3. Bound the comments leg by the anchor scan

- [ ] Test: the phase-2 scan stops at the first anchor whose source post
      id is newer than the posts cursor, and does not read past it.
- [ ] Test: a comment written later against an older post (its group id
      lies beyond the bound) is picked up in a later window, not dropped.
- [ ] Test: an anchor for an unsupported/skipped post does not stall the
      leg forever.
- [ ] Implement in `clone/comments.py` alongside the existing anchor
      recognition — the scan already has the post id in hand.

### 4. `--limit` across the interleaved legs

- [ ] Test: `--limit N` counts batches across both legs and may now return
      comments where it previously returned only posts; `sync.more` is
      `True` when the budget stopped either leg.
- [ ] Test: `--limit` smaller than one window still terminates and saves
      both cursors.
- [ ] CONTRACT §11: state that sync interleaves the legs in windows and
      that `--limit` spans them. Note ADR-0023's ordering clause is
      superseded by ADR-0051.

### 5. Release

- [ ] CHANGELOG + double version bump; MAP if a module gained a role;
      DEVLOG; PR → reviewer → CI → merge → tag.

### 6. Live acceptance (owner-gated, after merge)

- [ ] `[икона]` is no longer available as the acceptance subject: it was
      caught up on 1.2.9 on 2026-07-25 (posts cursor 94, `discussion_cursor`
      897, both at the source tails), so a resume there copies nothing and
      proves nothing. Interleaving needs a clone with both legs unfinished.
- [ ] Accept on a fresh clone of a small owner-controlled source with
      comments instead: `clone init` + `clone sync --limit` so the run stops
      mid-window, then confirm the destination group holds comments
      interleaved with anchors rather than anchors alone. Stop on exit 5;
      never retry in a loop (ADR-0045).
