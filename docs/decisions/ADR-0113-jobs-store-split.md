# ADR-0113: Split jobs store bootstrap from CRUD and transitions

Date: 2026-08-13
Status: accepted
Form: ADR-lite (ADR-0058)
Extends: [ADR-0087](ADR-0087-foreground-persisted-jobs.md) jobs registry layout.

## Context

Thermos debt ticket T29: `jobs/store.py` sat at the exact 919-line architecture
ceiling with SQLite bootstrap, schema DDL, connection policy, CRUD, the running
job state machine, lane flocks, and runtime-retry timing in one module. The next
jobs feature would force an integrator ratchet before any product work.

## Decision

1. Add `jobs/db.py` for schema v1 DDL, `BUSY_TIMEOUT_MS`, path resolution,
   connect / connect_existing / connect_mutating, meta bind, and the store
   inventory helper.
2. Keep `jobs/store.py` for CRUD, generation transitions, claim/requeue/complete/
   fail paths, crash recovery, and lane locks. Re-export the db connect surface
   so existing `from tgcli.jobs import store` call sites stay stable.
3. Move runtime failure retry policy constants
   (`RUNTIME_FAILURE_TERMINAL_STREAK`, first/second retry delays, terminal
   `not_before`) into `jobs/model.py` beside the other typed vocabulary.
4. Ratchet ceilings: `jobs/db.py` 280, `jobs/store.py` 665, `jobs/model.py` 55.

## Rejected alternatives

- Leave the monolith and ratchet `store.py` on the next feature: defeats the
  thermos debt goal and keeps bootstrap mixed with transitions.
- Move transitions into a third module now: YAGNI — store alone is under ceiling
  with headroom after the bootstrap split.

## Contract impact

None. CLI flags, JSON shapes, stdout/stderr, exit codes, and on-disk schema are
unchanged.
