# Deferred Issues

This file tracks deliberately deferred product work that should survive the
current implementation plan. Items here are not promises for the current
release.

Unvetted owner wishlist ideas that have **not** passed the maintenance-mode
gate live in [PROPOSALS.md](PROPOSALS.md); an item graduates to this file
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

**Status:** deferred by ADR-0028. **Re-entry trigger:** the first
recurring agent workflow that has to poll many chats on a schedule or
must detect edits/deletions — re-reading via `read --after-id` no longer
economical. Needs its own ADR: updates-state handling, gap recovery, and
cursor format are design work, not flag work.

Shape agreed in principle: a foreground command
(`tg changes --cursor C [--wait N]`) that returns
`{events: [...], next_cursor}` and exits — no daemon, consistent with
ADR-0002.

### Blocker: session-lock contention (found 2026-07-23)

The agreed shape has a hole that must be closed *before* the rest of the
design, because it can change the command's shape.

`session.client()` takes `LOCK_EX | LOCK_NB` per session file and holds it
for the whole invocation (`src/tgcli/session.py`). A poller that loops
`tg changes --wait 30` therefore owns the lock ~100% of the time, and every
other command on that account — `tg send`, `tg read`, `tg api` — fails with
"session is busy". That breaks exactly the workflow the feed exists to
enable: observe an event, fetch the peer, prepare a draft. A feed that
monopolises the account is worse than no feed.

wacli hit the same wall and solved it by delegation: when `sync --follow`
holds the store, `send` hands the message to that process instead of
erroring. That implies IPC, which for us is a daemon by another name
(ADR-0002). Three candidate resolutions, none free:

- **`--wait` yields the lock** between poll cycles; other commands get a
  bounded `--lock-wait` instead of instant failure. Keeps one session,
  costs reconnect churn and makes "busy" a timing lottery.
- **Second session for the poller** (own `.session`, own lock). Simplest and
  fully daemonless, but Telegram counts it as another authorized device, and
  ACCOUNTS-001 has to be able to create it.
- **Feed folded into `tg batch`** — one connection performs the poll *and*
  the follow-up reads, so contention never arises for the common case.
  Narrows the design to scripted consumers.

Whichever wins, `--wait` semantics and the lock contract are the same
decision and must be settled together.

### Design input from the wacli review (2026-07-23)

- **Deletions are events, not absences.** wacli never treats a vanished row
  as proof of deletion: deleted messages keep an explicit tombstone
  (`deleted_at`, `deletion_reason`) and a purge ledger prevents a later sync
  from resurrecting purged payloads. The feed equivalent is an explicit
  deletion event, whose exact type name and fields remain for the future ADR.
  A consumer must never have to infer a deletion from a re-read that came back
  shorter than expected.
- **A gap must be loud.** When the cursor cannot be honoured (updates-state
  too old, session gap), the result must report the gap instead of silently
  returning a short list that reads as "nothing happened". A
  `read --after-id` hint can recover newly created messages only; it cannot
  reconstruct edits or deletions of older messages. The future ADR therefore
  has to distinguish recoverable creation history from lost edit/deletion
  events and define an explicit rebaseline contract. The exact gap JSON waits
  for that decision rather than pretending one read closes every event class.
- **Story viewers are out of scope, deliberately.** The lead-generation
  workflow that makes a feed attractive does not arrive through updates at
  all: `stories.getStoryViewsList` is a poll-only read and is already
  read-allowlisted (`src/tgcli/commands/api.py`, ADR-0010). Scoping FEED-001
  as if it covered story viewers would build a subsystem for something it
  cannot deliver — check the raw call against the real scenario first.
