## 2026-08-09 — launchd template wall-clock bound for archive refresh

**Did:** added `--max-runtime 3000` to `docs/assets/tgcli-archive-refresh.plist`
and stated the scheduling rule in `docs/guide/archive-refresh.md`: a scheduled
pass's cap must stay shorter than its `StartInterval` (3600 s), and exhausting
the cap is a normal stop (exit 0, `stop_reason: "wall_clock_cap"`), not a
failure to alert on. Template and guidance only — no production code, no
CONTRACT change. Closes #152.

**Decided:** cap 3000 s keeps a 10-minute margin for the previous pass to
finish and release the session lock before the next launchd fire. Small-fix
lane, no ADR.

**Learned:** before 2.0.0 the ADR-0052 machinery bounded flood waits in the
foreground; that budget is gone, so the plist was the only place left to bound
wall time on a scheduled pass.

**Next:** the job-model question in #146 (lock contention, failure-streak
distinction) remains deliberately out of scope for this slice.
