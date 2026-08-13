## 2026-08-13 — Wave A merge: 3.0.2 (#243) (Composer)

**Did:** Squash-merged PR #243 (T02 / ADR-0090) onto `main` as 3.0.2 after
resolving conflicts with 3.0.1 (both ADR-0089 and ADR-0090 kept). Ratcheted
`gate.py`/`ledger.py` ceilings. Local gate before push.

**Decided:** Keep both governor fail-closed behaviours in one tree; index
rows and MAP range end at 0090.

**Learned:** Parallel Wave A PRs conflict on MAP/ADR-0072/gate/ledger/
test fixtures — merge order #242 then #243 is load-bearing.

**Next:** Merge #249 as 3.0.3, then #250 as 3.0.4.
