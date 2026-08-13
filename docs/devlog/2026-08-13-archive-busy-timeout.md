## 2026-08-13 — Archive SQLite contention policy (Cursor)

**Did:** pinned `BUSY_TIMEOUT_MS=5000` in `archive.store.connect`, passed the
same policy to `sqlite3.connect`, and explicitly issued `PRAGMA busy_timeout`
before WAL/schema setup. Replaced the default-sensitive regression with one
live-value assertion and one real-connection SQL trace. Removing the explicit
PRAGMA produced the intended red result: 1 failed, 1 passed; restoring it
produced 2 passed. Added ADR-0096, its index row, and the MAP inventory update.

**Decided:** full lane under ADR-0073 because this changes a released command's
persistent archive store behavior. ADR-0096 treats five seconds as explicit
tgcli policy, not as a claim that the value differs from CPython's default.

**Learned:** CPython already initializes SQLite's busy timeout to five seconds,
so querying only `PRAGMA busy_timeout` stays green even if tgcli removes its
explicit PRAGMA. A connection trace is needed to prove deliberate application.

**Next:** independent whole-diff Spec + Standards review, then integrator-owned
merge bookkeeping; no version or CHANGELOG edit belongs on this feature branch.
