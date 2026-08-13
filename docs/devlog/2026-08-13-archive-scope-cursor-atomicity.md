## 2026-08-13 — archive scope/cursor atomicity review fixes (Cursor agent)

**Did:** fixed the confirmed whole-diff findings on PR #255 for thermos T09
and T23. Added CLI regressions for a corrupt remove cursor, an in-flight sync
that finishes after remove, and an out-of-scope channel delete delivered
through `tg archive sync`.

The red tests showed that cursor corruption committed the scope delete and
that a stale sync restored the removed subscription. `archive.store` now owns
scope plus subscription removal in one `BEGIN IMMEDIATE` transaction.
Account-sync partial writes acquire the write lock before reading preserved
fields, and sync/rebaseline cursor writes project channel subscriptions onto
current explicit scope under that lock.

**Decided:** ADR-0110 records the persistent-state safety rule. No CLI flags,
JSON fields, exit codes, version files, CHANGELOG, or release tags changed.
T23 keeps its apply-time scope guard as defense in depth; its regression now
crosses argument parsing, dispatch, Telegram update mapping, SQLite apply, and
JSON output through the public CLI seam.

**Learned:** atomic file replacement is not the relevant primitive for the
archive database. The invariant needs one SQLite write transaction, and every
cursor writer that can race with removal must re-check scope while holding it.

**Next:** rerun the full gate and hand the complete PR diff back to independent
Spec + Standards review before merge.
