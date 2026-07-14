# ADR-0015: Truthful persistent mirror showcase

Status: accepted (2026-07-14).

Amends ADR-0014 without changing its lean production architecture.

## Context

Before mirroring valuable real channels, the operator wants to compare sources
and copies inside Telegram and decide whether the copies look sufficiently like
the originals. The expanded R1 laboratory proved Telegram capabilities and
native destination shapes, but its group, forum, and comment canaries seeded
source and destination independently. Those canaries are research evidence,
not evidence that the production mirror produced the destination.

Repeatedly creating and deleting disposable peers also caused long FloodWaits.
A separate showcase copy engine would duplicate production behavior and could
visually approve a path that real mirrors never use.

## Decision

### One production copy path

A showcase destination is created only by `tg mirror init SOURCE --commit` and
filled only by the production `tg mirror sync SOURCE` path. Fixtures are seeded
only in the source. Independent destination seeding can prove Telegram shape,
but can never produce `organic_copy_green` or `visual_approved`.

There is no mutating `mirror showcase` runner. A future showcase command, if
useful, is a read-only projection over mirror status and its review checklist.

### Persistent private ownership

An authorized mirror destination is a private, creator-owned,
`user_owned_retained` Telegram peer. It has no automatic expiry and is not a
cleanup obligation. Review expiry, cancellation, SIGINT, SIGTERM, or a process
crash never deletes it. Release is a separate future destructive command with
an exact mirror-id confirmation.

The retained destination is the showroom artifact. Reusing it avoids
create/delete churn and exercises the same state, mappings, random ids, and
transport that will be used for a real mirror.

### Promotion states

Each topology moves independently through:

```text
blocked -> candidate -> organic_copy_green -> visual_approved -> production_enabled
```

- `candidate`: its production vertical slice exists and focused tests pass.
- `organic_copy_green`: source-only fixtures reached the destination through
  production code and machine checks verified mappings, media/reply/topic
  structure, restart recovery, and zero silent omissions.
- `visual_approved`: the operator reviewed the native Telegram presentation
  against a versioned checklist.
- `production_enabled`: both previous verdicts are fresh for the implementation
  and Telegram compatibility fingerprint being released.

Visual approval expires after 30 days or immediately when presentation logic,
attribution fallback, topology code, checklist version, Telegram schema layer,
or Telethon pin changes. Expiry affects only the verdict, never the retained
Telegram peer.

### Topology order and honest blockers

The production order is:

1. unprotected broadcast channel: text, native media, albums, and replies;
2. channel plus plain linked comments;
3. protected broadcast reconstruction;
4. standalone supergroup;
5. standalone forum;
6. legacy basic group normalized to a private supergroup after the explicit
   two-account gate succeeds.

Channel plus forum discussion remains `blocked`: current controlled-live
evidence says Telegram does not accept the created forum as a discussion group.
No plain-group downgrade may be presented as forum parity.

### Creation and rate-limit safety

Before destination creation, tgcli persists the exact temporary marker and a
`reconcile_required` state. Loss of the response, cancellation, a signal, an
invalid response, or FloodWait leaves that state durable. A retry scans every
exact-title candidate before any create:

- one exact private creator-owned broadcast resumes;
- any wrong-shape exact-title candidate or multiple matches blocks;
- zero matches after a dispatched create never creates automatically.

A second create after an ambiguous zero-match result requires an expired
account-level cooldown plus explicit retry and exact mirror-id confirmation.
FloodWait persists the account-level `retry_not_before`; all mirror mutations
remain serial and fail locally until it expires. Account rotation is not a
rate-limit bypass.

## Consequences

- Visual evidence now proves the actual shipping path rather than a parallel
  laboratory renderer.
- Persistent private destinations reduce FloodWait pressure and remain useful
  for later regression reviews.
- Creation recovery becomes slightly more explicit, but it prevents duplicate
  orphan channels after ambiguous Telegram outcomes.
- The next implementation gate is destination-creation hardening, followed by
  the ADR-0014 native media/album/reply slice. No live mutation occurs before
  both are machine-green and reviewed.
