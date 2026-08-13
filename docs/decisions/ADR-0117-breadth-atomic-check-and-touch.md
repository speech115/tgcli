# ADR-0117: Atomic breadth check-and-touch in the governor ledger

Date: 2026-08-13
Status: accepted
Form: ADR-lite (safety / governor pacing; amends ADR-0072 decision 3)
Owner request: thermos debt T36
Amends: [ADR-0072](ADR-0072-account-request-governor.md) decision 3 — the
100-peer/24h breadth hedge must not be exceedable by a race between a
separate remaining check and a later peer touch.

## Context

ADR-0072's rolling breadth budget is account-scoped and shared across
session roles. Commands that walk peers called `budget_ok` (read
`breadth_remaining`) and later recorded the peer via `touch_peer` on the
history-read path. Two concurrent processes (primary + role job) that both
observed remaining==1 could both insert distinct peers and land the ledger
at 101 for a 100-peer hedge — the exact overlap the SQLite move was meant
to close for writers, but not yet for this check-then-touch split.

## Decision

1. Add `Ledger.try_touch_peer`: one `BEGIN IMMEDIATE` transaction counts
   peers in the rolling window, allows an already-counted peer to refresh
   `touched_at`, and inserts a *new* peer only when `count < budget`.
2. `pacing.touch_history_peer` spends budget only through `try_touch_peer`.
   Unconditional `touch_peer` remains for test seeding and non-budgeted
   refreshes. Its `bool` is True for a successful claim *or* a non-spend
   request; False means only "new history peer refused — budget full".
3. `budget_ok` stays an early-exit hint for walkers; it is not itself the
   race-free spend. The gate records via `touch_history_peer` but does not
   refuse dispatch on False. Concurrent proof bootstraps the on-disk
   schema once, then races two per-thread file-backed `Ledger.open`
   claims at remaining==1 with a small test budget (never race two cold
   opens on a missing file — that can degrade under ADR-0089).

## Rejected alternatives

- **Advisory lock / flock beside the ledger.** The peer table already
  lives in SQLite; a second lock would duplicate the writer serialization
  WAL + `BEGIN IMMEDIATE` already provide.
- **Make unconditional `touch_peer` refuse when over budget.** Seeds and
  re-touches used in tests and recovery paths would need a second API
  anyway; naming the atomic claim keeps the fail-open write path explicit.
- **Only document "do not overlap primary and role jobs".** The incident
  class is concurrent by design (ADR-0062 roles); the ledger must enforce
  the hedge.

## Contract impact

None: no CLI flag, JSON shape, or exit code changes. Internal governor
ledger semantics tighten so recorded peers in the 24h window cannot exceed
`BREADTH_BUDGET` under concurrency.
