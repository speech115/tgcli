# 2026-07-26 — ADR-0058 process-speed revisions (Claude Fable 5)

**Did:** implemented the process-speed slice the owner green-lit from the
PROPOSALS "Process-speed rule revisions" section. ADR-0058 accepted:
integrator-assigned version/CHANGELOG at merge (amends ADR-0038 rule 3
mechanics), per-session devlog files under `docs/devlog/` (this file is the
first), a +50-line grace band in `scripts/check-architecture.py` with a
`--strict` merge-time true-up flag, the wave-branching rule, and the
ADR-lite form. Grace band built red-first: four new tests
(warning-within-grace, boundary pass, past-grace fail, `--strict` fail)
failed before the implementation and pass after. AGENTS.md, CLAUDE.md,
MAP.md, DEVLOG.md header, ADR-0038 status, and the ADR index updated in
the same commit.

**Decided:** ADR-0058. `GRACE = 50` absolute (not a percentage): a
percentage would give the biggest files the most slack, which is backwards.
DEVLOG.md keeps the template so AGENTS.md has one canonical place to point
at.

**Learned:** the cherry-pick that rebased this branch onto post-campaign
`main` conflicted on exactly one file — `docs/DEVLOG.md` — a live
demonstration of the conflict class this slice removes.

**Next:** verification-infra slice (macOS CI leg, hypothesis properties,
xdist measurement) on the same branch.
