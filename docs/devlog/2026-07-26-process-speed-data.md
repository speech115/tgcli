# 2026-07-26 — Process-speed rule review recorded in PROPOSALS (Claude Fable 5)

Moved from `docs/DEVLOG.md` when ADR-0058 closed that file for appends in
the same branch: this entry was written under the old convention hours
earlier, and a closed file must not carry an entry dated after its close
(independent-review finding on PR #88).

**Did:** measured where change time actually goes — full `./scripts/gate.sh`
52 s (pytest 29.5 s / 1326 tests), merged-PR cycle median 1.2 h (p75 3.3 h,
max 11.7 h over 39 merged PRs), per-PR composition ≈2.8 test lines and ≈2
docs/process lines per `src/` line, shared-file churn (DEVLOG 54/133
commits, architecture ceilings 38, CONTRACT 33, CHANGELOG 27) — and
recorded six rule-revision candidates in PROPOSALS.md ("Process-speed rule
revisions"). Docs only; no code, no rule changed.

**Decided:** nothing adopted — every candidate stays behind the
maintenance-mode gate. Explicitly out of scope for revision: red-first
tests, boundary tests, independent pre-merge review, and gate-before-commit;
their measured hit rate (4 of 5 wave-1 slices needs-work, two confirmed
majors found on PR #77) is the reason the repo is healthy.

**Learned:** the "changes are slow" feeling is not test runtime or merge
latency — it is per-change docs volume plus serialization on
conflict-by-construction shared files. So the honest speedups are shape
changes (who bumps the version, one file per DEVLOG entry, ceiling
tolerance bands), not discipline cuts.

**Next:** owner picks which candidates graduate; the first three
(integrator-assigned releases, per-entry DEVLOG, ceiling tolerance) would
land together as one AGENTS.md + ADR slice.
