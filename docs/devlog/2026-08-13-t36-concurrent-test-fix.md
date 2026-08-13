## 2026-08-13 — T36 concurrent breadth test fix (Cursor)

**Did:** Independent review of PR #279 found the concurrent
`try_touch_peer` test flaky (~13%): racing `Ledger.open` could degrade
one thread to an in-memory ledger (ADR-0089), so both claims "won".
Rewrote the test to open two file-backed connections before the barrier.
Clarified `touch_history_peer` return: False only means budget-full new
peer; non-spend paths return True. Gate discards False deliberately
(walker early-exit via `budget_ok`). Added pacing-layer coverage;
updated ADR-0117 decision text.

**Decided:** Gate still does not refuse dispatch on a failed breadth
claim (ADR-0117); document at the call site rather than change safety.

**Learned:** Concurrent `Ledger.open` is not a valid serialization proof
for `BEGIN IMMEDIATE` — open first, then race the claim.

**Next:** Re-run Spec review on #279 tip; merge after green local gate.
