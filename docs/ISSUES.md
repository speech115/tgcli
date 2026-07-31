# Deferred Issues

This file tracks deliberately deferred product work that should survive the
current implementation plan. Items here are not promises for the current
release.

Unvetted owner wishlist ideas that have **not** passed the owner gate
(ADR-0071) live in [PROPOSALS.md](PROPOSALS.md); an item graduates to this file
once it has an owner request + ADR (as MSG-001 and FEED-001 already did).

## CLONE-001 — Poll cloning

**Status:** partially completed by ADR-0019 after clone v1 live acceptance.

`tg clone sync` now emits a truthful static snapshot instead of skipping a poll.
A controlled live native-forward canary proved that Telegram assigns a new poll
ID and resets non-zero source results to zero, so native forwarding cannot
preserve the requested historical result snapshot.

Before implementation, decide and document the fidelity contract:

- a recreated native poll cannot preserve original votes or voters;
- closed/open state, quiz answers, explanations, anonymity, and multiple-choice
  behavior need explicit live verification;
- the destination must keep a visible one-for-one position even when exact
  reconstruction is impossible (for example, by using a documented fallback).

Open single-choice and multiple-choice snapshots are covered by mocked tests and
live evidence from two real polls. Remaining follow-up: controlled closed-poll
and quiz fixtures, including correct-answer and explanation fidelity. This item
does not block clone.

## CLONE-002 — Forum topics and legacy groups

**Status:** closed by ADR-0022 (2026-07-16).

Forum megagroups clone into forum-megagroup destinations with a 1:1 lazy
topic map; live legacy basic groups clone like megagroups (broadcast
destination, attributed transport); bot dialogs are accepted dialog sources.
Migrated or deactivated basic groups are rejected with pointer messages.
Still out of scope: cloning into pre-existing groups, secret chats,
topic edit/close propagation.

## CLONE-003 — Windowed interleaving of the posts and comments legs

**Status:** closed by ADR-0051 (re-entered and implemented 2026-07-25 via
PR #74). `clone sync` interleaves posts and comments in 50-batch windows;
an unmapped cross-leg parent beyond the posts cursor defers instead of
flattening. The comments-unstarted stderr warning from the deferral period
remains.

**Was:** deferred the same day the ADR landed, before code; a stderr warning
answered the one live "duplicated posts" report without touching the write
path. Re-entry came from the owner directly, asked and answered during the
merge review of PR #74 — not from the abandoned-clone evidence gate, which
was never met, and not from the PR's own say-so.

## ACCOUNTS-001 — `tg accounts login`: session (re)authorization

**Status:** closed by ADR-0042 (2026-07-24). Shipped as `1.2.0`:
`accounts login` (QR default, phone+code fallback, native-dialog /
`--password-stdin` cloud password), plus `accounts show` and
`accounts remove` so the account lifecycle closes.

**Still unmet (operational, not code):** keep `~/.config/tgcli/` and
`~/.local/state/tgcli/` inside the machine backup. Verified unmet on the
owner's machine on 2026-07-24 (`AutoBackup = 0`, destination fails to
mount). Recovery no longer depends on a hand-written Telethon script, but
a lost disk still costs a full re-authorization of every account.

## MSG-001 — Messaging tail: albums, scheduling, reactions, pin

**Status:** partially completed by ADR-0030 (outgoing `--format` +
`custom_emoji` harvest). **Remaining / re-entry trigger:** the first real
agent task that needs one of the leftovers, named explicitly by the owner.

The v1.1 working set (ADR-0028) covers reply, single file with caption,
forum topic, silent, edit, delete, forward, mark-read. ADR-0030 added
`--format {plain,md,html}` on `send`/`edit` and additive `custom_emoji` on
the universal message JSON. Still deferred: albums (`--album a.jpg b.jpg`),
scheduled sends, `react`, `pin`, protect-content, and a raw `entities`
passthrough. Each lands as flags or a small command under the existing
preview→commit model; none needs a new subsystem.

## FEED-001 — `tg changes`: daemonless change feed

**Status:** closed by ADR-0063 and released in `1.2.19` on 2026-07-27,
after live acceptance of send/edit/delete events and `--wait` on an
ADR-0062 named session role. Shipped shape: hybrid coverage (cursor-held
channel subscriptions + `channel_activity` signals), opaque `v1:` cursor,
`read`-shape event bodies, loud per-scope gaps, deletion tombstones,
`--wait N` + fixed 2 s settle, and no state files.

Originally deferred by ADR-0028; re-entered 2026-07-27 when ADR-0062 and
ADR-0063 were accepted.

### Blocker: session-lock contention (found 2026-07-23) — resolved

Resolved by ADR-0062 named session roles (accepted 2026-07-27). The poller
runs on `--session-role job` while the primary stays free.

### Design input from the wacli review (2026-07-23)

- **Deletions are events, not absences.** wacli never treats a vanished row
  as proof of deletion: deleted messages keep an explicit tombstone
  (`deleted_at`, `deletion_reason`) and a purge ledger prevents a later sync
  from resurrecting purged payloads. ADR-0063 adopted the feed equivalent as
  an explicit `message_delete` tombstone. A consumer never has to infer a
  deletion from a re-read that came back shorter than expected.
- **A gap must be loud.** When the cursor cannot be honoured (updates-state
  too old, session gap), the result must report the gap instead of silently
  returning a short list that reads as "nothing happened". A
  `read --after-id` hint can recover newly created messages only; it cannot
  reconstruct edits or deletions of older messages. ADR-0063 therefore
  distinguishes recoverable creation history from lost edit/deletion events
  and defines an explicit per-scope rebaseline contract.
- **Story viewers are out of scope, deliberately.** The lead-generation
  workflow that makes a feed attractive does not arrive through updates at
  all: `stories.getStoryViewsList` is a poll-only read and is already
  read-allowlisted (`src/tgcli/commands/api.py`, ADR-0010). Scoping FEED-001
  as if it covered story viewers would build a subsystem for something it
  cannot deliver — check the raw call against the real scenario first.

### Design input from the clone/runtime discussion (2026-07-26)

The contention is not only the future poller's problem: a long
`tg clone sync` holds the account's session lock for its whole run today,
so `send` / `read` / `api` on the same account fail with "session is
busy" until the clone finishes. An owner-side design discussion
(2026-07-26) explored three shapes, matching and extending the
candidates above:

- **Secondary session for long jobs** (e.g. a `main-clone` alias): fully
  daemonless and possible under today's architecture; costs another
  authorized device in Telegram's list, and `accounts login` must create
  it. The only candidate that changes no process model.
- **Yielding the lock between clone windows.** `sync` already works in
  50-batch windows (ADR-0051), so it could release/reacquire the lock at
  window boundaries; costs reconnect churn per window, interacts with
  cooldown state, and makes "busy" a timing lottery — the same objection
  as the `--wait` variant above.
- **A single session-owner runtime** (`tg runtime start`, local IPC
  socket, priority command queue, background clone jobs, process-wide
  FloodWait gate). Architecturally the complete answer for continuous
  cloning plus interactive commands on one session — and explicitly a
  **daemon**: it contradicts ADR-0002 and the AGENTS.md hard rule, so it
  can only enter through an ADR that overturns that line deliberately,
  as a new subsystem (runtime lifecycle, IPC, queue, crash recovery) —
  never as a side effect of a lock tweak. The per-run FloodGate
  (1.2.16) would have to become runtime-wide.

Two fixed points regardless of the winner: one session file shared by
multiple concurrent processes stays forbidden (SQLite session corruption
risks the authorization itself), so naive lock removal is not an option;
and whichever shape wins must be decided together with `--wait`
semantics (the blocker above) — they are the same lock contract.
