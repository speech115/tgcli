## 2026-07-31 — Archive Phase 1 review fix: flood seam + boundary tests (Cursor Grok)

**Did:** Closed Task 1 Important findings on `codex/archive-store`. Wired
archive backfill through `cooled_account` + `arm_account` / `WaitBudget`
(ADR-0045/0052) with mid-iter FloodWait checkpoint + exit 5 tests; added
offline alias-mismatch (exit 2) and pre-init NOT_FOUND (exit 4) CLI
regressions; cheap minors for readonly+backfill and CONTRACT/guide notes on
sequential partial persistence and FLOOD_WAIT exit 5. Report:
`.superpowers/sdd/2026-07-31-archive-store/task-1-review-fix1.md`.

**Decided:** No new ADR. `with_cooldown` stays CloneState-bound; archive
reuses account flood record + WaitBudget resume for the iter_messages
generator seam (residual gap documented in the fix report).

**Learned:** Checkpoint + RateLimitError alone was not enough against the
plan Global Constraint — other commands already refuse an armed account
cooldown before RPC.

**Next:** Independent whole-diff Spec+Standards re-review of the Phase 1
slice; integrator ratchets architecture ceilings + release bookkeeping.
