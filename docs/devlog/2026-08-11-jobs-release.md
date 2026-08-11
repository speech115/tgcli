## 2026-08-11 — Foreground jobs integration and 3.0.0 (Codex)
**Did:** integrated reviewed PRs #190, #191, #192, and #194 on
`codex/issue-146-jobs`; prepared the breaking 3.0.0 release for ADR-0087 and
ADR-0088. Authorized a dedicated `job` role by phone. Live local acceptance
completed one transcription item without error. A bounded Telegram
archive-sync quantum applied 1846 updates, preserved its next cursor, recorded
one pre-existing channel gap, and stayed queued for remaining media work with
`failure_streak=0` and `stop_reason=wall_clock_cap`.
Whole-campaign review found four defects and each gained a reproducing test:
the Telegram lane now locks before opening its role session; archive progress
hashes per-dialog cursors; existing jobs directories are repaired to `0700`;
and schema/supersession docs match the cutover. Release gate: 1881 passed, 9
skipped; coverage, docs, pyright, ruff, and
architecture checks passed.
**Decided:** did not run clone acceptance. The two accessible candidates were
behind their source or discussion cursor, while the other recorded sources
were no longer accessible to the role; none was a provable no-op. Creating a
new fixture or running either candidate would publish to Telegram and was
outside the non-publishing approval.
**Learned:** archive `max_events` bounds catch-up reads, not the already-paid
Telegram difference; applying all 1846 received events before advancing the
cursor is the lossless behavior the contract requires.
**Next:** full-campaign review and head-SHA CI, squash merge to `main`, then
verify the automated `v3.0.0` tag and GitHub Release.
