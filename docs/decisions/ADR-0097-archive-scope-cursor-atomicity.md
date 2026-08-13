# ADR-0097: Archive scope and cursor mutation is atomic

Date: 2026-08-13
Status: accepted

## Context

T09 requires `archive remove` to remove a channel from both explicit archive
scope and the persisted account changes cursor. The first implementation
committed the scope deletion before decoding and rewriting the cursor. A
corrupt cursor therefore left the scope removed while the subscription
remained. An `archive sync` already in flight could also persist the cursor it
read before removal and restore that subscription. The two rows are one safety
invariant even though they live in separate tables.

`account_sync` partial updates had the same stale read-modify-write window:
they read all preserved fields before opening a write transaction. A reconcile
write could consequently restore a cursor changed by another connection.

## Decision

1. `archive.store.remove_scope` owns removal of both explicit scope and the
   matching cursor subscription. It opens `BEGIN IMMEDIATE`, decodes the cursor
   before deleting anything, writes both changes, and commits once. Any decode
   or SQLite failure rolls the whole operation back.
2. General `account_sync` updates acquire the write transaction before reading
   preserved fields. A caller already in a write transaction keeps that
   transaction, so existing compound store writes remain one commit.
3. `archive sync` and `archive rebaseline` persist candidate cursors through a
   scoped writer. While holding `BEGIN IMMEDIATE`, it intersects channel
   subscriptions with the current explicit scope immediately before writing.
   A concurrent remove and a stale network result therefore serialize in
   either order without restoring an out-of-scope subscription.
4. T23's apply-time scope check remains defense in depth. Its permanent
   regression runs through the public `archive sync` CLI seam and verifies
   that an out-of-scope channel delete does not insert a tombstone.

No SQLite transaction is held across a Telegram RPC.

## Rejected alternatives

- Keep sequential `remove_scope` and cursor helper calls: cursor decode or
  write failure still commits half of the invariant.
- Rely only on T23's event guard: it avoids the tombstone but retains stale
  channel polling and does not repair the persisted subscription.
- Hold the archive write transaction for the complete sync: network latency
  would block all local archive writers and turn FloodWait into lock
  starvation.
- Add a cursor generation column: the current-scope projection under the same
  write lock resolves this race without a schema migration.

## Contract impact

No flags, JSON fields, or exit codes change. A corrupt persisted cursor still
produces the existing policy error, but `archive remove` now leaves scope
unchanged instead of partially committing. Concurrent sync/remove operations
preserve the documented explicit-scope boundary.

## Consequences

- The archive store, not the command layer, owns the scope/cursor invariant.
- Cursor persistence returns the actual scope-filtered cursor, keeping
  `archive sync` and `archive rebaseline` output aligned with durable state.
- This is a persistent-state and released-command safety correction, so it
  takes the ADR-0073 full lane and requires the full gate plus independent
  whole-diff review.
