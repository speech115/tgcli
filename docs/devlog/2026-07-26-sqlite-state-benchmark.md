# 2026-07-26 — bf-19 measured: clone-state SQLite proposal (Claude Fable 5)

**Did:** built `scripts/bench-clone-state.py` — offline, no Telegram —
replaying today's exact per-message state write (real `to_dict()` +
`atomic.replace_text`, fsync included) against a SQLite/WAL prototype.
Measured: JSON at 5k messages costs 15.2 s and 167.9 MB written and grows
quadratically (5× messages → 26× bytes); SQLite at 5k costs 0.24 s and
0.1 MB, and handles 50k in 2.46 s. Wrote ADR-0060 (**proposed**) with the
table, the migration requirements (versioned schema, JSON import +
backup, rollback, crash tests), and the explicitly rejected cheap fix.

**Decided:** nothing final — ADR-0060 is proposed, not accepted: a storage
migration needs the owner's yes. Save-per-batch was rejected *in the
proposal* on safety grounds: it shrinks I/O by widening the crash window
to a whole batch, which is the duplicate-post class clone exists to avoid.

**Learned:** the SQLite prototype's per-message transaction keeps the
same at-most-one-message crash loss as today's design while removing the
quadratic term entirely — the two goals were never actually in tension;
only the file format was.

**Next:** owner accepts/rejects ADR-0060; if accepted, a scoped plan with
migration + crash tests + disposable-clone live acceptance.
