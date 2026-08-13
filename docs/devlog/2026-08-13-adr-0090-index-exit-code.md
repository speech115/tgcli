## 2026-08-13 — ADR-0090 index row exit-code drift (Composer)

**Did:** Short thermos on PR #243 tip `0cc2157` flagged the ADR index
summary still claiming exit 2 from the rejected PolicyError draft.
Rewrote `docs/decisions/README.md` row 0090 to match the ADR body:
sticky deadline, re-raised FloodWait, next same-type exit 5.

**Decided:** Index row is a merge blocker when it contradicts the ADR
body it summarizes, even when check-docs cannot catch content drift.

**Learned:** Rewriting an ADR mid-PR must include the README index row
in the same commit as the body rewrite.

**Next:** Wave A merge after owner OK (#242, #243, #249, #250).
