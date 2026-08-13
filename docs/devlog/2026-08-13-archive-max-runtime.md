## 2026-08-13 — Archive max-runtime through media (T10 / ADR-0106) (Composer)

**Did:** Thread `should_stop` / `pacing.wall_clock_remaining` through CLI
archive backfill+sync (dispatch), job backfill quanta, and `fetch_media`.
Regression: capped backfill does not start a media tail. CONTRACT media
note + ADR-0106.

**Decided:** Fail closed on media after the wall-clock cap; difference
events still apply in full before media (existing sync rule).

**Learned:** Jobs already had the seam; CLI sync was the hole.

**Next:** Independent whole-diff review before merge.
