## 2026-08-13 — Breadth atomic check-and-touch (Cursor)

**Did:** T36 — added `Ledger.try_touch_peer` (`BEGIN IMMEDIATE` count +
insert) so concurrent remaining==1 claims cannot both succeed; wired
`pacing.touch_history_peer` through it; concurrent + unit tests;
ADR-0117 + index/MAP; ratchet ledger/pacing ceilings for `--strict`.

**Decided:** ADR-0117 (amends ADR-0072 decision 3): authoritative breadth
spend is atomic check-and-touch; `budget_ok` stays an early-exit hint.

**Learned:** Unconditional `touch_peer` must remain for test seeding; the
gated path needs a distinct name so fail-open writes stay explicit.

**Next:** Independent whole-diff Spec + Standards review before merge.
