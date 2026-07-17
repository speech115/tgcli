# ADR-0026: Maintenance mode — default posture after v1 completion

Date: 2026-07-17
Status: accepted

## Context

Phases 0–7 of the master plan completed on 2026-07-10 and the clone
feature passed its round-3/3.5 live gates on 2026-07-17. The owner has
declared the project feature-complete: everything planned works in
production, and future work is expected to be bug fixes and incremental
improvements only.

The coordination docs still described a phased build, which misroutes
future agent sessions: AGENTS.md "Read First" pointed to PLAN.md as "the
current phase and scope" although PLAN.md is a historical record; PLAN.md's
Non-Goals bullet had accreted the whole clone chronicle and had already
drifted from reality (it still claimed the ADR-0023 comments live gate was
pending after that gate passed); the ADR index lived at the bottom of
MAP.md and had drifted too (stopped at 0023, no status column). Drift in
two documents within one week of maintenance is the failure mode to fix.

## Decision

1. The project is in **maintenance mode**. Default posture: do not add
   features. A bug fix starts from a reproducing test. Any new feature or
   behavior change requires an explicit owner request plus an ADR and a
   scoped plan — never a new phase in PLAN.md.
2. AGENTS.md "Read First" routes current scope to docs/ISSUES.md and the
   ADR index; PLAN.md and the clone chronicle are historical background.
3. The clone chronicle moves from PLAN.md's Non-Goals bullet to
   docs/CLONE.md; PLAN.md keeps a short pointer.
4. The ADR index moves from docs/MAP.md to docs/decisions/README.md and
   gains a Status column. Adding an ADR requires adding its index row in
   the same commit (extends ADR-0007 discipline).

## Consequences

- A future session opening AGENTS.md learns the project posture in one
  screen and cannot mistake completed phases for open scope.
- ADR archaeology no longer requires opening files to learn which
  decisions are live; the index states supersessions explicitly.
- One canonical index instead of two prevents the observed drift.
- DEVLOG/MAP/CONTRACT discipline (ADR-0007) is unchanged — maintenance
  sessions still log every session.
