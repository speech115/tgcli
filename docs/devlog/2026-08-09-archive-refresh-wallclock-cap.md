## 2026-08-09 — launchd template wall-clock bound for archive refresh

**Did:** added `--max-runtime 3000` to
`docs/assets/tgcli-archive-refresh.plist` and stated the scheduling rule in
`docs/guide/archive-refresh.md`: a scheduled pass's cap stays below its
`StartInterval` (3600 s), and exhausting the cap is a normal stop (exit 0,
`stop_reason: "wall_clock_cap"`), not a failure to alert on. Closes #152.

**Decided:** the template keeps the cap below the interval so a wake that
finds the cap already exhausted defers sync instead of starting a pass that
cannot finish. The cap is checked once, before dispatch (CONTRACT §13), and
never interrupts a running pass — the hard bounds on a scheduled pass are
the per-command caps. A true mid-run wall-clock bound is proposed in
PROPOSALS.md (full lane, changes CONTRACT §13). Small-fix lane, no ADR.

**Learned:** a wall-clock cap that is checked only before dispatch cannot
bound a pass that already started; every launchd fire is a fresh process
with a fresh budget, so the "10-minute margin" framing in the first version
of this entry was wrong and has been corrected here and in the guide/plist.
Also: XML comments must not contain double hyphens, so the plist comment
names the flag without the `--` prefix.

**Next:** the job-model question in #146 (lock contention, failure-streak
distinction) remains deliberately out of scope for this slice.
