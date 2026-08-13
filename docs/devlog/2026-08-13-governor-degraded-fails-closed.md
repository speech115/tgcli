## 2026-08-13 — Governor degraded ledger fails closed (Composer)

**Did:** ADR-0089 + gate `refuse_if_degraded` (`PolicyError` exit 2) when
`ledger.degraded` on authenticated RPCs; doctor treats
`governor_degraded: true` as `ok: false`. CONTRACT §5.1 updated. Repro
tests in `test_governor_gate` / `test_cli_doctor`. Pre-auth and
`doctor --connect` stay ungated. Addresses thermos T01 / #205.

**Decided:** Keep non-raising `Ledger.open()` for diagnosis; fail closed
at the governed `_call` seam, not inside open. Exit 2 not exit 5.

**Learned:** ADR-0072 L3 intentionally failed open — thermos P0 and owner
request reverse that for authenticated traffic only.

**Next:** #206 arm_cooldown persist fail-closed; integrator bumps patch
version for CONTRACT change on merge.
