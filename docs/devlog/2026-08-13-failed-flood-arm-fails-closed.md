## 2026-08-13 — Failed FloodWait arm fails closed (Composer)

**Did:** ADR-0090 + sticky `Ledger.remember_cooldown`; `arm_from_flood`
retries then returns False; gate raises `PolicyError` (exit 2). Tests in
`test_governor_gate`. CONTRACT §4. Addresses thermos T02 / #206.

**Decided:** Process-local sticky deadline + PolicyError, not ledger-wide
degraded and not bare FloodWait re-raise.

**Learned:** sqlite3.Connection methods are not monkeypatchable; seam
tests stub `arm_cooldown` and rely on `remember_cooldown` in the gate.

**Next:** #205 (T01) ADR-0089 on sibling PR; then T03 media completeness.
