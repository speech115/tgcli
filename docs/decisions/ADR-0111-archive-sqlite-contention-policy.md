# ADR-0111: Pin the archive SQLite contention policy

Date: 2026-08-13
Status: accepted
Form: ADR-lite (ADR-0058)
Extends: [ADR-0068](ADR-0068-local-archive-store.md) store connection policy.

## Context

The released archive commands share one per-account SQLite/WAL store, and
search, sync, and transcription can overlap across foreground processes.
Unlike the jobs and governor stores, `archive.store.connect()` did not state a
contention policy. CPython currently gives `sqlite3.connect()` a five-second
default timeout, so merely observing `PRAGMA busy_timeout = 5000` cannot prove
that tgcli chose or applied that value. A persistent store must not inherit a
driver default as accidental policy.

## Decision

1. `archive.store` names `BUSY_TIMEOUT_MS = 5_000` as its connection policy.
2. `connect()` passes the same policy to `sqlite3.connect()` in seconds and
   explicitly executes `PRAGMA busy_timeout = BUSY_TIMEOUT_MS` before the WAL
   and schema setup.
3. WAL still permits concurrent readers and serializes writers. This is a
   bounded SQLite wait, not an application retry loop or a promise that
   multiple archive writers make progress concurrently.
4. Regression coverage queries the live connection value against the named
   constant and traces a real connection to prove that `connect()` issued the
   PRAGMA. The test does not claim that the value differs from SQLite or
   CPython defaults.

## Rejected alternatives

- Rely on CPython's current five-second default: the observed value would be
  right for the wrong reason and could drift with a runtime or constructor
  change.
- Use a timing-based lock-contention test as the policy proof: it would add
  scheduler sensitivity while still not proving that tgcli deliberately
  issued the PRAGMA.
- Add retries, a longer wait, or an archive-wide process lock: those are
  different concurrency policies and are not justified by thermos finding
  T24.

## Contract impact

None. CLI flags, JSON shapes, stdout/stderr, and exit codes do not change, so
`docs/CONTRACT.md` is unchanged. The decision pins internal behavior of the
existing persistent archive store.
