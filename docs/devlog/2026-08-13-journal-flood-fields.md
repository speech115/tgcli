## 2026-08-13 — Journal flood fields only on flood exits (Composer)

**Did:** Attach `pacing.last_stop()` journal fields only when
`exit_code == 5` or `error_code == FLOOD_WAIT` (thermos T16). Regression
in `test_governor_pacing`.

**Decided:** Small-fix restoring CONTRACT §9; no ADR.

**Learned:** Exit-0 survived-flood case was already covered; the hole was
nonzero non-flood exits.

**Next:** Continue thermos P2 backlog.
