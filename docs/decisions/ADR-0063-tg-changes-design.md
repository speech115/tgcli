# ADR-0063: `tg changes` — daemonless change feed design

Date: 2026-07-26
Status: accepted (2026-07-27, amended before acceptance: hybrid channel
coverage, cursor-held subscriptions, full event bodies, settle window,
`read_marker` dropped; FEED-001 re-entry; depends on ADR-0062 for the
lock contract)

## Context

FEED-001 (deferred by ADR-0028) is the one deferred feature that changes
what the tool *is*: without it an agent polls every chat with
`read --after-id`; with it, a workflow can observe → read → act. The
agreed shape — a foreground command that returns events and a cursor,
then exits — has waited on one blocker (session-lock contention, now
addressed by the ADR-0062 job-session proposal) and on the design inputs
recorded in ISSUES.md from the wacli review: deletions must be events,
gaps must be loud, story viewers are out of scope.

## Proposal

`tg changes --cursor C [--wait N] [--session-role job]`, foreground, one
JSON document, exit 0:

```json
{"events": [...], "next_cursor": "…", "gap": null}
```

- **Hybrid coverage** (the load-bearing amendment). Telegram's common
  updates state (`updates.getState` / `updates.getDifference`) covers
  private dialogs and basic groups only; every channel/supergroup has
  its own `pts` and requires `updates.getChannelDifference`. So the
  feed has two tiers: **subscribed channels** get full events; every
  other channel yields a `channel_activity` signal (ids only — "this
  channel changed; the cursor does not cover it"), sourced from the
  `updateChannelTooLong`-class updates in the common difference. The
  primary consumers (agent workflows, future live clone sync) watch a
  handful of named channels; the signal tier keeps the rest observable
  without per-channel polling.
- **Cursor** is an opaque, internally versioned string (`v1:…`)
  encoding the common state (`pts`/`qts`/`date`) plus a
  `{channel_id: pts}` map for subscribed channels. The subscription
  set lives **in the cursor** — the caller passes only the cursor and
  keeps everything it watches; a forgotten flag can never silently drop
  a channel. `tg changes --init [--peer P …]` returns
  `{"events": [], "next_cursor": …}` — the explicit baseline call; a
  missing or malformed cursor is exit 2, never a silent full-history
  replay. On a regular call `--peer P` **adds** a subscription
  (baselined at the current channel `pts`, no history replay — noted in
  the output) and `--drop-peer P` removes one; set changes happen only
  through these explicit operations.
- **Event vocabulary v1**, additive forever: `message_new`,
  `message_edit` (both carry the full message body in the exact
  `tg read` JSON shape — `getDifference` already delivers it, and one
  entity must not grow a second contract form; a truncated body from
  Telegram is marked as such, never padded), `message_delete` (a
  tombstone with ids only — Telegram does not deliver the deleted body;
  the consumer must never infer a deletion from a shorter re-read), and
  `channel_activity` (ids only, see above). Every event carries the
  marked (`-100…`) peer id per CONTRACT §. `read_marker` was dropped
  from v1 (no consumer; YAGNI) — like every other unhandled update
  class it is counted in the `skipped` object so silence is measurable
  and future demand is visible before it is built.
- **Gaps are loud, per scope.** When Telegram answers
  `differenceTooLong` (common state) or
  `channelDifferenceTooLong` (one subscribed channel), the result
  carries a gap object naming its scope:
  `{"gap": {"scope": "common" | <peer id>, "reason": "…", "recover": {...}}, "next_cursor": <rebased>}`.
  Creation history is recoverable via the per-chat `read --after-id`
  hint in `recover`; edit/deletion history in the gap window is lost and
  the document says so. A channel-scope gap rebases only that channel's
  `pts`. Exit 0 — a gap is data, not an error.
- **`--wait N`** long-polls up to N seconds for the first event, then
  waits a fixed 2-second settle window (bounded by the remaining
  deadline — N is never exceeded) to batch a burst before returning; an
  active chat yields one call per batch, not one call per message. The
  window is a documented constant, not a flag; a `--settle` knob can be
  added additively if real demand appears. Without `--wait` the command
  returns immediately with whatever is pending — no settle. Intended to
  run on an ADR-0062 session role; on the primary session it works but
  monopolizes the lock — documented, not forbidden (single-account
  users may accept it for a one-off).
- **No state files.** The cursor lives with the caller; tgcli stores
  nothing between invocations (ADR-0002 posture preserved).

## Rejected

- A daemon / `--follow` mode (ADR-0002).
- Deletion-by-absence semantics (wacli lesson, recorded in ISSUES.md).
- Story viewers in scope: `stories.getStoryViewsList` is poll-only and
  already read-allowlisted; a feed cannot deliver it.
- An sqlite events mirror: Telegram already stores history; a mirror is
  a second source of truth (wacli-review conclusion).

## Consequences

- New read command → CONTRACT section, guide page, SKILL routing,
  FEATURES matrix row for the `updates` namespace, boundary tests
  asserting the exact `getDifference` **and `getChannelDifference`**
  request types, and a release (third in the
  SQLite → session-roles → `tg changes` sequence; tagged only after
  live acceptance: send/edit/delete on a test channel observed through
  the feed).
- FEED-001 closes; its ISSUES.md entry records the shipped shape.
- The `--events` NDJSON idea from ADR-0040 stays deferred: this ADR's
  single-document contract serves the scripted consumer; a streaming
  variant remains future work if a real workflow demands it.
